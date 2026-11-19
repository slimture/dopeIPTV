"""SAT>IP servers: DVB-S/S2, DVB-C and DVB-T tuners on the network that
stream what they tune over RTSP - Digital Devices' Octopus NET, Telestar's
Digibit, Kathrein's EXIP, minisatip, a Fritz!Box Cable, Tvheadend's SAT>IP
server.

Found by SSDP: an M-SEARCH for ``urn:ses-com:device:SatIPServer:1``,
multicast on the local network and unicast (UPnP 1.1) to the addresses of
the other networks Nerd lists (core/home_net.py) as lan_scan.py asks them,
and to the server's own address when it is typed. Its device description
names it (``friendlyName``), says its tuners per system
(``satip:X_SATIPCAP``: "DVBS2-4,DVBT-2" is four for DVB-S/S2 and two for
DVB-T) and, where it has one, its channel list (``satip:X_SATIPM3U``).

Channels: an M3U the person gives - a file or a web address of SAT>IP
addresses, as lists are published for each satellite and cable network,
several one after the other - else the server's own list (X_SATIPM3U),
else a Fritz!Box's (``/dvb/m3u/tvhd.m3u``, ``tvsd.m3u``, ``radio.m3u``,
each its own group). Grouped by ``group-title``, with the logos
(``tvg-logo``), numbers (``tvg-chno``, or "12. Name") and guide ids
(``tvg-id``) a list gives. A channel's id comes from its tuning: the system,
the frontend source (``src``, the DiSEqC position), frequency, polarisation
and PLP, and its first service PID (the PMT, which lists put first): the
list reordered, a channel renamed or an audio track added, a favourite
stays. An address that tunes is put on the server's own address and RTSP
port: lists published for everyone name ``sat.ip`` or an example address.
A server's own other addresses (Tvheadend's ``/stream/channelid/...``) are
kept, one naming the loopback put on the server's address.

Playback: the RTSP address (``rtsp://host:554/?src=1&freq=11494&pol=h&
msys=dvbs2&sr=22000&pids=0,...``), opened by mpv and ffmpeg themselves -
over TCP, interleaved in the RTSP connection, unless the source says UDP
(core/rtsp.py sets it for that stream alone) - or, where the server streams
over HTTP too, the same query on its HTTP port (``transport`` "http",
``http_port``), which the rewind buffer can hold.

Tuners: each stream takes a tuner of its system, and X_SATIPCAP's number is
how many there are; a person may give fewer (``tuners``), which also caps
the streams of all systems together - for a server whose tuners do two
systems and are listed under both. ``tuner_verdict`` counts the app's own
streams from the server against that (core/tuners.py's ``counted``), and
the window asks before one more would find none free
(ui/mw_connections.py); with no number said, before a second. What other
devices on the network take from the server is not known here: it is not
counted.

Guide: SAT>IP carries none. The guide is the source's own guide address
(XMLTV), or the ``url-tvg`` of the M3U it was given; ``guide_note`` says
so plainly when there is neither.

Checked against what is known of SES's SAT>IP Protocol Specification
(1.2.2) - there is no SAT>IP server here: the search target, the
description's ``X_SATIPCAP`` and ``X_SATIPM3U`` in the ``urn:ses-com:satip``
namespace, the systems DVBS2, DVBT, DVBT2, DVBC and DVBC2, the query's
parameters (src, fe, freq, pol, ro, msys, mtype, plts, sr, fec, bw, tmode,
gi, plp, t2id, sm, specinv, pids), RTSP on port 554, OPTIONS setting nothing
up - how a server with no description is told apart here, no tuner taken.
Not checked against a real server: where a description is kept when no
search finds it (minisatip ``:8080/desc.xml``, a Fritz!Box
``:49000/satipdesc.xml``, Tvheadend ``:9981/satip_server/desc.xml``,
Octopus NET ``/octoserve/octonet.xml``), a Fritz!Box's lists and their
``avm=1`` (kept as given), which servers take RTSP over TCP (most do; the
source can say UDP), which stream over HTTP and on what port (8875 unless
the source names one; minisatip's is 8080), and whether a server shares a
tuner between two streams of one transponder (counted as two here).
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from ..core import rtsp, safe_xml, tuners
from ..core._lazy_requests import requests
from ..core.log import log
from ..i18n import LocalizedError, tr
from . import device_urls, dlna
from .boxes import USER_AGENT, BoxClient, clean_text, parse_xml

ST = "urn:ses-com:device:SatIPServer:1"
SATIP_NS = "urn:ses-com:satip"
RTSP_PORT = 554
HTTP_PORT = 8875
SSDP_PORT = 1900
#: The systems X_SATIPCAP names, as a person reads them.
LABELS = {"DVBS2": "DVB-S/S2", "DVBT": "DVB-T", "DVBT2": "DVB-T2", "DVBC": "DVB-C",
          "DVBC2": "DVB-C2"}
#: A query's msys and the systems whose tuners play it, the first one
#: listed taken: a DVB-T2 tuner plays DVB-T where a server lists no DVB-T.
POOLS = {"dvbs": ("DVBS2",), "dvbs2": ("DVBS2",), "dvbt": ("DVBT", "DVBT2"),
         "dvbt2": ("DVBT2",), "dvbc": ("DVBC", "DVBC2"), "dvbc2": ("DVBC2",)}
_FAMILY = {"dvbs": "S", "dvbs2": "S", "dvbt": "T", "dvbt2": "T", "dvbc": "C", "dvbc2": "C"}
#: Where a server keeps its description when no search finds it (port,
#: path): minisatip, a Fritz!Box, Tvheadend's SAT>IP server, Octopus NET.
DESCRIPTION_PATHS = ((8080, "/desc.xml"), (49000, "/satipdesc.xml"),
                     (9981, "/satip_server/desc.xml"), (80, "/octoserve/octonet.xml"))
#: A Fritz!Box's own lists, each its own group.
FRITZ_LISTS = (("/dvb/m3u/tvhd.m3u", "satip_tv_hd"), ("/dvb/m3u/tvsd.m3u", "satip_tv_sd"),
               ("/dvb/m3u/radio.m3u", "satip_radio"))
#: Hosts a list names that are no server's: the published lists' stand-in,
#: the loopback, every address.
_STAND_INS = ("sat.ip", "localhost", "0.0.0.0", "::", "::1")
MAX_XML = 512 * 1024
#: The most a channel list file is read: a satellite's every service is a
#: few MB.
LIST_MAX = 256 << 20
TRANSPORTS = ("tcp", "udp", "http")

_EXTINF = re.compile(r'#EXTINF:\s*-?[\d.]*(?P<attrs>(?:[^,"]|"[^"]*")*),(?P<name>.*)')
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_NUMBERED = re.compile(r"^(\d{1,4})\.\s+(.+)$")
_LISTS = re.compile(r"[\s,;|]+(?=(?:https?://|file:))", re.I)


# -- what a server says ---------------------------------------------------------------

def parse_caps(text: str | None) -> dict[str, int]:
    """X_SATIPCAP's tuners per system: "DVBS2-4,DVBT-2" -> {"DVBS2": 4,
    "DVBT": 2}. An entry that says no number is left out; DVBS (some write
    it) is DVBS2."""
    out: dict[str, int] = {}
    for part in re.split(r"[,;\s]+", text or ""):
        m = re.fullmatch(r"([A-Za-z0-9]+)-(\d{1,3})", part.strip())
        if not m:
            continue
        system = m.group(1).upper()
        system = "DVBS2" if system == "DVBS" else system
        n = int(m.group(2))
        if n > 0:
            out[system] = out.get(system, 0) + n
    return out


def caps_text(caps: dict[str, int]) -> str:
    """"DVB-S/S2 ×4, DVB-T ×2" - the tuners as a person reads them."""
    return ", ".join(f"{LABELS.get(k, k)} ×{n}" for k, n in caps.items())


def _local(tag: Any) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


def parse_description(content: bytes, location: str) -> dict | None:
    """A device description, when it is a SAT>IP server's: {"name",
    "model", "manufacturer", "udn", "caps", "m3u" (absolute, or ""),
    "location"}; None for anything else (a router's, a TV's), and for one
    that declares a DTD of its own (core/safe_xml.py). Its list is taken
    over http or https on the server's own host (*location*'s) - never a
    file of this computer, nor another host (providers/device_urls.py)."""
    if not content or len(content) > MAX_XML:
        return None
    try:
        safe_xml.refuse_dtd(content, "A SAT>IP description")
        root = parse_xml(content)
    except (ET.ParseError, ValueError):
        return None
    for el in root.iter():
        el.tag = _local(el.tag)
    base = (root.findtext("URLBase") or "").strip() or location
    for dev in root.iter("device"):
        if "satipserver" not in (dev.findtext("deviceType") or "").lower():
            continue
        caps: dict[str, int] = {}
        for el in dev.iter("X_SATIPCAP"):
            for k, n in parse_caps(el.text).items():
                caps[k] = caps.get(k, 0) + n
        m3u = (dev.findtext("X_SATIPM3U") or "").strip()
        try:
            m3u = device_urls.on_host(urljoin(base, m3u), device_urls.host_of(location)) if m3u else ""
        except ValueError:
            m3u = ""
        return {"name": clean_text(dev.findtext("friendlyName")),
                "model": clean_text(dev.findtext("modelName")),
                "manufacturer": clean_text(dev.findtext("manufacturer")),
                "udn": (dev.findtext("UDN") or "").strip(),
                "caps": caps, "m3u": m3u, "location": location}
    return None


def _netloc(host: str, port: int | None) -> str:
    h = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{h}:{port}" if port else h


def describe(location: str, session: Any = None, timeout: tuple[float, float] = (2.0, 5.0)
             ) -> dict | None:
    """The SAT>IP server whose description is at *location*, or None. As it
    stands - no redirect followed - and read no further than MAX_XML, as a
    DLNA description is (providers/dlna.py)."""
    http = session or requests
    try:
        r = http.get(location, timeout=timeout, headers={"User-Agent": USER_AGENT},
                     allow_redirects=False, stream=True)
        if r.status_code != 200:
            r.close()
            return None
        return parse_description(dlna._read_body(r, MAX_XML), location)
    except Exception as e:
        log.debug("satip: no description at %s (%s)", location, type(e).__name__)
        return None


def rtsp_options(host: str, port: int = RTSP_PORT, timeout: float = 2.0) -> dict | None:
    """What an RTSP server says to OPTIONS - which sets nothing up and
    takes no tuner: {"status", "public", "server"}; None when nothing
    answers RTSP there (core/rtsp.py)."""
    return rtsp.options(host, port, timeout, USER_AGENT)


# -- finding servers ----------------------------------------------------------------

def _entry(desc: dict) -> dict:
    host = urlsplit(desc["location"]).hostname or ""
    return {"kind": "satip", "server": f"http://{_netloc(host, None)}",
            "name": f"{desc['name'] or 'SAT>IP'} - {host}", "description": desc["location"],
            "caps": caps_text(desc["caps"])}


def _described(locations: Iterable[str], cancel: threading.Event | None = None) -> list[dict]:
    locs = [loc for loc in dict.fromkeys(locations) if loc.startswith("http://")]
    if not locs or (cancel is not None and cancel.is_set()):
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(locs))) as pool:
        got = list(pool.map(describe, locs))
    found: dict[str, dict] = {}
    for desc in got:
        if desc is not None:
            e = _entry(desc)
            found.setdefault(desc["udn"] or e["server"], e)
    return list(found.values())


def discover(timeout: float = 1.5, addr: tuple[str, int] = dlna.SSDP_ADDR) -> list[dict]:
    """Every SAT>IP server that answers a multicast search here:
    [{"kind": "satip", "server", "name", "description", "caps"}]."""
    return _described(dlna.ssdp_search(timeout, addr, targets=(ST,)))


def discover_at(hosts: list[str], timeout: float = 1.5, port: int = SSDP_PORT,
                cancel: threading.Event | None = None) -> list[dict]:
    """The SAT>IP servers at *hosts* (addresses), each asked directly - a
    unicast M-SEARCH reaches another part of a network split in VLANs."""
    if not hosts:
        return []
    got = dlna.ssdp_unicast(hosts, timeout, port, cancel, targets=(ST,))
    return _described([loc for locs in got.values() for loc in locs], cancel)


# -- the channel list -----------------------------------------------------------------

def split_lists(text: str | None) -> list[str]:
    """Several lists in one field: one a line, or separated by commas or
    spaces before the next web or file:// address. A file's path keeps its
    spaces."""
    t = (text or "").strip()
    if not t:
        return []
    return [p.strip() for line in t.splitlines() for p in _LISTS.split(line) if p.strip()]


def parse_m3u(text: str) -> tuple[list[dict], str]:
    """([{name, url, group, logo, tvg_id, chno}], the url-tvg of its header)."""
    out: list[dict] = []
    guide = ""
    pending: dict | None = None
    group = ""
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            hdr = dict(_ATTR.findall(line))
            guide = guide or (hdr.get("url-tvg") or hdr.get("x-tvg-url") or "").split(",")[0].strip()
        elif line.startswith("#EXTINF"):
            m = _EXTINF.match(line)
            attrs = dict(_ATTR.findall(m.group("attrs"))) if m else {}
            name = (m.group("name") if m else line.split(",", 1)[-1]).strip() or attrs.get("tvg-name", "")
            chno = attrs.get("tvg-chno", "").strip()
            pending = {"name": name, "group": attrs.get("group-title", ""), "logo": attrs.get("tvg-logo", ""),
                       "tvg_id": attrs.get("tvg-id", ""), "chno": int(chno) if chno.isdigit() else 0}
        elif line.startswith("#EXTGRP:"):
            group = line[len("#EXTGRP:"):].strip()
        elif not line.startswith("#"):
            entry = pending or {"name": "", "group": "", "logo": "", "tvg_id": "", "chno": 0}
            entry["url"] = line
            entry["group"] = entry["group"] or group
            out.append(entry)
            pending = None
    return out, guide


def tuning(url: str | None) -> dict[str, str] | None:
    """The tuning a SAT>IP address asks for, its parameters by name (lower
    case) - None for an address that tunes nothing itself."""
    try:
        u = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    if u.scheme.lower() not in ("rtsp", "http", "https") or not u.query:
        return None
    q = {k.lower(): v.strip() for k, v in parse_qsl(u.query, keep_blank_values=True)}
    return q if ("freq" in q or "msys" in q) else None


def system_of(url: str | None) -> str:
    """The delivery system an address tunes ("dvbs2", "dvbc"...), "" when
    it says none."""
    q = tuning(url) or {}
    return q.get("msys", "").lower()


def pool_of(msys: str, caps: dict[str, int]) -> str | None:
    """The X_SATIPCAP system whose tuners play *msys* on a server with
    *caps*; None when it lists none for it (or says no tuners at all)."""
    return next((p for p in POOLS.get(msys, ()) if caps.get(p)), None)


def _freq(value: str) -> str:
    try:
        f = float(value)
    except ValueError:
        return value.strip()
    return f"{f:g}"


def service_pid(pids: str) -> str:
    """The first PID of a service in a pids list - its PMT, where lists put
    it first - leaving out the tables every transponder carries (0-31) and
    the stuffing (8191); "" when there is none."""
    for p in (pids or "").split(","):
        p = p.strip()
        if p.isdigit() and 32 <= int(p) < 8191:
            return str(int(p))
    return ""


def channel_key(url: str, name: str = "") -> str:
    """What names a channel for good: its tuning - system family, source,
    frequency, polarisation, PLP - and its service's first PID; a server's
    own address by its path and query (not its host)."""
    q = tuning(url)
    if q is None:
        try:
            u = urlsplit(url)
            return f"satip|{u.path}?{u.query}"
        except ValueError:
            return f"satip|{url}"
    family = _FAMILY.get(q.get("msys", "").lower(), "")
    src = q.get("src") or ("1" if family == "S" else "")
    pol = q.get("pol", "").lower() if family == "S" else ""
    pid = service_pid(q.get("pids", "")) or name.casefold()
    return "|".join(("satip", family, src, _freq(q.get("freq", "")), pol, q.get("plp", ""), pid))


# -- the client ---------------------------------------------------------------------

class SatIPClient(BoxClient):
    LOG_NAME = "satip"
    KIND = "SAT>IP"
    DEFAULT_PORT = 80
    TIMEOUT = (4.0, 30.0)
    SSDP_PORT = SSDP_PORT
    #: What each server said of its tuners this session, by host: another
    #: source of the same server (another satellite's list) counts on it
    #: before it has loaded.
    _seen_caps: dict[str, dict[str, int]] = {}

    def __init__(self, server: str, username: str = "", password: str = "",
                 options: dict | None = None) -> None:
        typed = (server or "").strip()
        super().__init__(typed, "", "", options)
        self.rtsp_port = RTSP_PORT
        try:
            u = urlsplit(typed if "://" in typed else "http://" + typed)
            host, port, path = u.hostname or "", u.port, u.path
            scheme = u.scheme.lower()
        except ValueError:
            host, port, path, scheme = "", None, "", "http"
        if host:
            # rtsp://host:port, or a bare host:554: the RTSP port.
            if scheme in ("rtsp", "rtsps") or ("://" not in typed and port == RTSP_PORT):
                self.rtsp_port = port or RTSP_PORT
                port = None
            if path.lower().endswith(".xml"):
                # A description's address typed: the server is its host
                # (its own web side, a Fritz!Box's lists, on its usual port).
                self.options.setdefault("description", urlunsplit(
                    ("http", _netloc(host, port), path, "", "")))
                port = None
            self.server = f"http://{_netloc(host, port)}"
        self.caps: dict[str, int] = dict(self._seen_caps.get(host.lower(), {}))
        self.description: dict = {}
        self.guide_note = ""
        # Where the description was found: a refresh asks there first.
        self._found_at = ""

    # -- the server --------------------------------------------------------------------

    def host(self) -> str:
        return (urlsplit(self.server).hostname or "").lower()

    @property
    def test_address(self) -> str:
        """Where Test connection's network steps go: the RTSP port, which
        every SAT>IP server has (its web side may be elsewhere)."""
        host = self.host()
        return f"rtsp://{_netloc(host, self.rtsp_port)}" if host else ""

    def transport(self) -> str:
        t = str(self.opt("transport", "tcp")).strip().lower()
        return t if t in TRANSPORTS else "tcp"

    def http_port(self) -> int:
        port = self.opt_int("http_port", HTTP_PORT)
        return port if 0 < port < 65536 else HTTP_PORT

    def _probe(self) -> dict:
        self.guide_note = ""
        desc = self._find_description()
        host = self.host()
        if desc is None:
            said = rtsp_options(host, self.rtsp_port)
            if said is None and not split_lists(self.opt("m3u", "")):
                raise LocalizedError("err_box_not_found", kind=self.KIND)
            self.description = {}
            name = clean_text((said or {}).get("server")) or "SAT>IP"
            return {"kind": "satip", "name": f"{name} ({host})", "tuners": self.tuner_limit() or 0,
                    "caps": caps_text(self.caps)}
        self.description = desc
        self._found_at = desc["location"]
        if desc["caps"]:
            self.caps = dict(desc["caps"])
            type(self)._seen_caps[host] = dict(self.caps)
        return {"kind": "satip", "name": desc["name"] or f"SAT>IP ({host})", "model": desc["model"],
                "manufacturer": desc["manufacturer"], "caps": caps_text(self.caps),
                "tuners": self.tuner_limit() or 0, "m3u": desc["m3u"]}

    def _find_description(self) -> dict | None:
        """The server's description: where it was found before, else where
        a search sent to its address says, else where servers keep one."""
        host = self.host()
        if not host:
            return None
        tried: set[str] = set()

        def at(location: str) -> dict | None:
            if not location or location in tried:
                return None
            tried.add(location)
            return describe(location, self.session)
        for location in (self._found_at, str(self.opt("description", "") or "")):
            known = at(location)
            if known is not None:
                return known
        try:
            said = dlna.ssdp_unicast([host], 1.5, self.SSDP_PORT, targets=(ST,))
        except Exception as e:
            log.debug("satip: no search sent to the server (%s)", type(e).__name__)
            said = {}
        for location in [loc for locs in said.values() for loc in locs]:
            got = at(location)
            if got is not None:
                return got
        for port, path in DESCRIPTION_PATHS:
            got = at(f"http://{_netloc(host, None if port == 80 else port)}{path}")
            if got is not None:
                return got
        return None

    # -- the channels ------------------------------------------------------------------

    def _lists(self) -> tuple[list[tuple[str, str]], bool]:
        """([(list address, its group or "")], whether the person gave them)."""
        given = split_lists(self.opt("m3u", ""))
        if given:
            return [(a, "") for a in given], True
        own = self._named_list()
        if own:
            return [(own, "")], False
        return [(self.url(path), tr(key)) for path, key in FRITZ_LISTS], False

    def _named_list(self) -> str:
        """The list the server's description names, on the server's own host
        over http(s) - else "" (providers/device_urls.py)."""
        return device_urls.on_host(self.description.get("m3u"),
                                   device_urls.host_of(self.description.get("location")) or self.host())

    def _read_list(self, address: str, named: bool = False) -> str:
        """A channel list's text: one the person gave - a web address, or a
        file of this computer (a path or file://) - or a Fritz!Box's on the
        server they gave; or, *named*, the one the server's description
        names: its word, read over http or https on the server and nowhere
        a redirect points - never a file."""
        if named:
            if not address.lower().startswith(("http://", "https://")):
                raise ValueError("a server's own list is read over http only")
            r = self.session.get(address, timeout=(4.0, 60.0), allow_redirects=False)
            r.raise_for_status()
            if r.status_code != 200:
                raise ValueError("the server's list is elsewhere")
            data = r.content
        elif address.lower().startswith(("http://", "https://")):
            data = self.get(address, absolute=True, timeout=(4.0, 60.0)).content
        else:
            path = address
            if address.lower().startswith("file:"):
                from urllib.request import url2pathname
                path = url2pathname(urlsplit(address).path)
            with open(path, "rb") as fh:
                data = fh.read(LIST_MAX + 1)
            if len(data) > LIST_MAX:
                raise ValueError("the list is larger than any channel list")
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("cp1252", errors="replace")

    def _load(self) -> tuple[list[dict], list[dict]]:
        lists, given = self._lists()
        rows: list[dict] = []
        keys: set[str] = set()
        read = 0
        guide = ""
        named = "" if given else self._named_list()
        for address, group in lists:
            try:
                text = self._read_list(address, bool(named) and address == named)
            except LocalizedError:
                raise
            except Exception as e:
                log.info("satip: a channel list was not read (%s)", type(e).__name__)
                continue
            entries, tvg = parse_m3u(text)
            if not entries:
                continue
            read += 1
            guide = guide or tvg
            rows += self._channels(entries, group, keys)
        if not read:
            raise LocalizedError("err_satip_list_unread" if given else "err_satip_no_list")
        self.epg_url = guide
        self.guide_note = "" if guide else tr("satip_no_guide")
        return rows, []

    def _channels(self, entries: list[dict], group: str, keys: set[str]) -> list[dict]:
        everything = group or tr("box_channels")
        rows: list[dict] = []
        for e in entries:
            url = self.play_address(e["url"])
            if not url:
                continue
            name, num = clean_text(e["name"]), int(e.get("chno") or 0)
            m = _NUMBERED.match(name)
            if m:
                num, name = num or int(m.group(1)), m.group(2).strip()
            key = channel_key(e["url"], name)
            if key in keys:
                # Two services sharing a PID (a regional variant): the whole
                # list of PIDs, then the name, tells them apart.
                key = f"{key}|{(tuning(e['url']) or {}).get('pids', '')}"
                if key in keys:
                    key = f"{key}|{name.casefold()}"
            keys.add(key)
            rows.append({"key": key, "name": name or "?", "category": clean_text(e["group"]) or everything,
                         "url": url, "icon": e["logo"], "epg_id": e["tvg_id"], "num": num})
        return rows

    def play_address(self, raw: str) -> str:
        """An address from a list as this server plays it: a tuning on the
        server's own address (its RTSP port, or its HTTP one where the source
        streams over HTTP); a server's own other address kept, one naming the
        loopback put on the server's address. "" for no address at all."""
        try:
            u = urlsplit((raw or "").strip())
            port = u.port
        except ValueError:
            return ""
        scheme = u.scheme.lower()
        host = self.host()
        if not scheme or not host:
            return ""
        if tuning(raw) is not None and scheme in ("rtsp", "http", "https"):
            path = u.path or "/"
            if self.transport() == "http":
                return f"http://{_netloc(host, self.http_port())}{path}?{u.query}"
            rport = port if scheme == "rtsp" and port else self.rtsp_port
            return f"rtsp://{_netloc(host, rport)}{path}?{u.query}"
        if (u.hostname or "").lower() in _STAND_INS or (u.hostname or "").startswith("127."):
            return urlunsplit((u.scheme, _netloc(host, port), u.path, u.query, ""))
        return raw.strip()

    # -- the tuners -------------------------------------------------------------------

    def own_stream(self, url: str | None) -> bool:
        """Whether *url* streams from this server."""
        try:
            u = urlsplit(str(url or ""))
        except ValueError:
            return False
        return (u.scheme.lower() in ("rtsp", "http", "https") and bool(self.host())
                and (u.hostname or "").lower() == self.host())

    def tuner_limit(self) -> int | None:
        """How many streams the server plays at once as far as is known: its
        tuners, or fewer where the source says so; None when neither says."""
        total = sum(self.caps.values())
        mine = self.opt_int("tuners", 0)
        if mine > 0:
            return min(total, mine) if total else mine
        return total or None

    def tuner_verdict(self, url: str, uses: Iterable[Any]) -> tuners.Verdict | None:
        """One more stream of *url* next to *uses* (core/connections.Use: the
        app's own as they will be): the Verdict when the tuners it needs are
        all held by the app's own streams from this server - those of its
        system, or every one - or, their number not known, one is held
        already; None when there is room, or *url* is not this server's."""
        if not self.own_stream(url):
            return None
        own = [u for u in uses if getattr(u, "url", "") and self.own_stream(u.url)]
        held = [tuners.Tune(u.kind, "", f"{u.kind}:{i}") for i, u in enumerate(own)]
        pool = pool_of(system_of(url), self.caps)
        if pool:
            mine = self.opt_int("tuners", 0)
            limit = min(self.caps[pool], mine) if mine > 0 else self.caps[pool]
            same = [t for t, u in zip(held, own, strict=True) if pool_of(system_of(u.url), self.caps) == pool]
            v = tuners.counted(limit, same, LABELS.get(pool, pool))
            if v.kind == tuners.CUTS:
                return v
        v = tuners.counted(self.tuner_limit(), held)
        return v if v.kind == tuners.CUTS else None
