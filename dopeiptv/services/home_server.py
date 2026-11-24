"""Server mode: dopeIPTV as the home's server as well as its player - what
Threadfin, xTeVe and Dispatcharr are for, in the same program.

Other devices on the home network watch the person's sources through it:
VLC, Kodi or a TV's IPTV app from the playlist (``/playlist.m3u``), Plex,
Jellyfin, Emby and Channels DVR as from an HDHomeRun tuner (core/hdhr.py:
``discover.json``, ``lineup.json``, the UDP discovery on port 65001), with
the guide the app downloaded under the lineup's numbers (``/xmltv.xml``).
Each channel is fetched once, here - with the source's user agent,
headers, own DNS, proxy, rewrite rules - and shared by everyone watching
it (core/restream.py); never more channels at once than the account allows
(the tuners), and the app's own viewing and recordings always first.

Started with the window (Nerd > Remote and automation, off by default) or
without one (``dopeiptv --server``, dopeiptv/headless.py); either way the
household's sources and settings, never a profile's own. What it serves:
the sources chosen, and in each the categories chosen - never a hidden,
locked or adult one, nor a channel hidden by hand. Who: an address on the
home network only (core/home_net.py). The HDHomeRun endpoints take the
token in the tuner's own address (``/t/<token>/``) unless the person lets
devices in without it (K_OPEN: only those listed, or - what a server set up
before 1.6.9 keeps - every device of this computer's own networks and, while
Nerd is on, the other networks it lists: home_net.LocalNetworks, never every
private range); the UDP discovery answers only those. A server that kept
the open tuner unasked is said so once (``open_untold``: the window's card,
whose "Close it" lets in only the devices that used it so, ``closed_mode``;
the log at every start of ``--server``). Everything else needs the token -
the settings page in a browser (``/?token=…``, core/server_page.py), the
playlist, the guide and the stream addresses
(``/stream/<source id>/<stream id>?token=…``).

The server knows the app only through its *host* (Host below): the window
(ui/mw_server.py) or the headless runner.

Its port taken (``port_taken``): who holds it is asked once, on loopback
(``look_at_port``: its ``discover.json``, nothing else) - dopeIPTV's own
server says so in every answer (``MARK``: the window's or a ``--server``'s),
one before 1.6.1 by its name - and the next port free is found by binding
(``next_free_port``), the app's other servers' ports left out
(``avoided_ports``). The caller says it; the port changes only when the
person chooses it.
"""
from __future__ import annotations

import errno
import ipaddress
import json
import re
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable, Iterator, Protocol
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..core import connections, hdhr, home_gate, home_net
from ..core.dvr_pool import DvrPool
from ..core.dvr_session import DvrSession
from ..core.exclusive_bind import ConnectionLimitMixin, ExclusiveBindMixin, IdleTimeoutMixin
from ..core.log import log, redact_url
from ..core.nerd_check import FAIL, OK, WARN, Check
from ..core.home_gate import RateLimiter, cross_site
from ..core.remote_api import lan_addresses
from ..core.restream import BOX, NO_DATA, TUNERS, VPN, Busy, Restreamer, ffmpeg_opener, is_hls
from .guide_keeper import GuideKeeper
from .share import Share, kept_out, lineup_guide, public_logo

DEFAULT_PORT = 3675             # the remote API's 3673, the phone remote's 3674, and one
MAX_TUNERS = 64
K_ON = "nerd_server"
K_PORT = "nerd_server_port"
K_TUNERS = "nerd_server_tuners"
K_SOURCES = "nerd_server_sources"
K_TOKEN = "nerd_server_token"
#: The page in a browser and its api/ - what it is doing, who watches what,
#: and its settings: a token of their own, so that the playlist's token
#: (handed to every TV and player, in every address of the playlist) never
#: changes the server.
K_ADMIN = "nerd_server_admin_token"
K_DEVICE = "nerd_server_device_id"
#: Channels and categories for adults handed out too (off: left out).
K_ADULT = "nerd_server_adult"
#: Who may use the tuner without its token (OPEN_*): nobody - the tuner's
#: address then carries the token, /t/<token>/ - only the devices at the
#: addresses in K_OPEN_HOSTS, or every device on this computer's own
#: networks (what server mode did before 1.6.9). A media server that cannot
#: take an address with a path (Plex) needs one of the last two.
K_OPEN = "nerd_server_open"
K_OPEN_HOSTS = "nerd_server_open_hosts"
#: The person knows the tuner is open to every device of this computer's
#: networks and keeps it so: the window's card that says so (``open_told``)
#: was dismissed, or the mode was picked in Settings. Unset for a server set
#: up before 1.6.9 that kept it unasked.
K_OPEN_TOLD = "nerd_server_open_told"
OPEN_OFF = "off"
OPEN_DEVICES = "devices"
OPEN_HOME = "home"
OPEN_MODES = (OPEN_OFF, OPEN_DEVICES, OPEN_HOME)
#: At most this many devices let in by address.
OPEN_HOSTS_MAX = 32
MAX_BODY = 64 * 1024
#: A stream whose client reads nothing for this long is let go.
STREAM_WRITE_TIMEOUT = 30.0
#: Any other answer (a page, the lineup, the guide) and a request's body: a
#: client that takes or sends nothing for this long is let go, with what was
#: made for it.
ANSWER_TIMEOUT = 60.0
#: The tuner's endpoints need no token, so no wrong one ever shuts an address
#: out: each address may ask them this often instead (a second, and at
#: once). A media server asks a few times a minute, and once per stream.
TUNER_RATE = 10.0
TUNER_BURST = 60.0
#: The lineup is built again after this long (a source's list is kept longer).
LINEUP_TTL = 60.0
HDHR_PATHS = ("/discover.json", "/lineup.json", "/lineup_status.json", "/device.xml",
              "/lineup.post")
_AUTO = re.compile(r"^/auto/v([0-9]{1,6}(?:\.[0-9]{1,3})?)$")
#: The tuner under its token: /t/<token>/discover.json, /t/<token>/auto/v5.
_TOKENED = re.compile(r"^/t/([A-Za-z0-9_-]{16,128})(/.*)?$")
SAFE_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY"}
#: In every answer: dopeIPTV's own server, and whose - its host's ``kind``
#: (HOLDER_WINDOW, HOLDER_HEADLESS), else "server".
MARK = "X-dopeIPTV-Server"
#: Who holds a port (look_at_port): dopeIPTV's server in the window, one
#: started as ``--server``, one that does not say which (before 1.6.1), or
#: another program.
HOLDER_WINDOW = "window"
HOLDER_HEADLESS = "headless"
HOLDER_OURS = "dopeiptv"
HOLDER_OTHER = "other"
#: Windows' WSAEADDRINUSE, whichever errno a Python there gives with it.
_WSAEADDRINUSE = 10048


# -- the settings ------------------------------------------------------------------------

@dataclass
class Config:
    port: int = DEFAULT_PORT
    #: Channels at once; 0: what each account allows (else one).
    tuners: int = 0
    #: The sources served and in each the categories ([]: every one); empty:
    #: the active source, every category.
    sources: dict[str, list[str]] = field(default_factory=dict)
    token: str = ""
    #: The page's and its api's own (K_ADMIN); the playlist's token does not
    #: open them.
    admin: str = ""
    device_id: str = ""
    #: Channels for adults in the lineup and the share (``kept_out``); off,
    #: as a TV in a child's room has no profile or PIN.
    adult: bool = False
    #: Who may use the tuner without its token (K_OPEN): OPEN_OFF by default.
    open: str = OPEN_OFF
    #: OPEN_DEVICES: the addresses let in, as ip_address strings.
    open_hosts: tuple[str, ...] = ()


def parse_sources(raw: Any) -> dict[str, list[str]]:
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        data = {}
    out: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for pid, cats in data.items():
            if isinstance(cats, list):
                out[str(pid)] = [str(c) for c in cats if str(c)]
    return out


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def read_flag(value: Any) -> bool:
    """A switch as QSettings hands it back (a bool, or "true"/"false")."""
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


def _plain_address(text: str) -> str | None:
    """*text* as an address is written in K_OPEN_HOSTS: an IPv4 address
    written as IPv6 (::ffff:192.168.1.7) as itself, a zone left out; None
    for no address. By ipv4_mapped, never by its string: Python 3.12.3 and
    older write that one ::ffff:c0a8:107."""
    try:
        ip = ipaddress.ip_address(str(text).split("%")[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ip)


def parse_open_hosts(text: Any) -> tuple[list[str], list[str]]:
    """The devices let in by address (K_OPEN_HOSTS): one IPv4 or IPv6
    address per line or separated by commas or spaces - a home address, or
    this computer's own (a media server beside the app), at most
    OPEN_HOSTS_MAX. (the addresses, the parts refused)."""
    good: list[str] = []
    bad: list[str] = []
    for part in re.split(r"[\s,;]+", str(text or "")):
        part = part.strip().strip("[]")
        if not part:
            continue
        try:
            ip = ipaddress.ip_address(part.split("%")[0])
        except ValueError:
            bad.append(part[:64])
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped             # judged as itself: an older Python calls every one private
        if not (ip.is_private or ip.is_link_local or ip.is_loopback) or ip.is_unspecified \
                or ip.is_multicast:
            bad.append(part[:64])
            continue
        norm = str(ip)
        if norm not in good and len(good) < OPEN_HOSTS_MAX:
            good.append(norm)
    return good, bad


def open_mode(settings: Any, ran_before: bool = False) -> str:
    """Who may use the tuner without its token (K_OPEN). Never set before:
    every device on this computer's networks where server mode was already
    on - or, *ran_before*, had run (``dopeiptv --server`` needs no switch:
    its device id is there) - as it was until 1.6.9, so a media server set
    up then keeps its tuner; else nobody. Written, so it stays what it is."""
    mode = str(settings.value(K_OPEN, "") or "").strip().lower()
    if mode in OPEN_MODES:
        return mode
    mode = open_mode_of(settings, ran_before)
    settings.setValue(K_OPEN, mode)
    return mode


def open_mode_of(settings: Any, ran_before: bool = False) -> str:
    """open_mode without writing it: what the tuner is open to now, as a
    page that only shows it reads it."""
    mode = str(settings.value(K_OPEN, "") or "").strip().lower()
    if mode in OPEN_MODES:
        return mode
    was = read_flag(settings.value(K_ON, False)) or ran_before
    return OPEN_HOME if was else OPEN_OFF


def open_told(settings: Any) -> bool:
    """Whether the person was told the tuner is open to every device of
    this computer's networks and keeps it (K_OPEN_TOLD)."""
    return read_flag(settings.value(K_OPEN_TOLD, False))


def open_untold(settings: Any) -> bool:
    """Whether the tuner is open to every device of this computer's
    networks without the person having been told - what a server set up
    before 1.6.9 kept (``open_mode``): the window says so once."""
    return open_mode_of(settings) == OPEN_HOME and not open_told(settings)


def closed_mode(seen: Iterable[str], kept: Any = "") -> tuple[str, str]:
    """(K_OPEN, K_OPEN_HOSTS) once the open tuner is closed: the devices
    that used it without the token (*seen*, this run) and those listed
    before (*kept*, K_OPEN_HOSTS) let in by their address alone
    (OPEN_DEVICES) - or, when none is known, nobody (OPEN_OFF): a device
    then takes the tuner's address with its token, or is let in by its
    address in Settings."""
    hosts, _bad = parse_open_hosts(" ".join([str(kept or ""), *map(str, seen)]))
    if not hosts:
        return OPEN_OFF, str(kept or "")
    return OPEN_DEVICES, ", ".join(hosts)


def exposure(mode: str, hosts: Iterable[str] = (), networks: Iterable[Any] = ()) -> tuple[str, dict]:
    """What the tuner is open to, in the person's words: (an i18n key, its
    values) - nobody without the token (*mode* OPEN_OFF), the devices
    listed (*hosts*), or every device of this computer's networks
    (*networks*, as they are read now)."""
    if mode == OPEN_HOME:
        nets = ", ".join(str(n) for n in networks)
        return ("srv_open_now_home", {"nets": nets}) if nets else ("srv_open_home_warn", {})
    if mode == OPEN_DEVICES:
        shown = ", ".join(hosts)
        return ("srv_open_now_devices", {"hosts": shown}) if shown else ("srv_open_hosts_none", {})
    return "srv_open_now_off", {}


def home_networks() -> list[Any]:
    """This computer's own home networks the open tuner answers (as
    home_net.LocalNetworks reads them), for the words that say so."""
    try:
        return [n for n in home_net.own_networks() if home_net.trusted(n)]
    except Exception:
        return []


def read_config(settings: Any) -> Config:
    """The server's settings; a token and a device id made the first time."""
    token = str(settings.value(K_TOKEN, "") or "").strip()
    if not token:
        token = secrets.token_urlsafe(24)
        settings.setValue(K_TOKEN, token)
    admin = str(settings.value(K_ADMIN, "") or "").strip()
    if not admin or admin == token:
        admin = secrets.token_urlsafe(24)
        settings.setValue(K_ADMIN, admin)
    device = str(settings.value(K_DEVICE, "") or "").strip().upper()
    if not hdhr.valid_device_id(device):
        device = hdhr.new_device_id()
        settings.setValue(K_DEVICE, device)
    return Config(port=_int(settings.value(K_PORT, DEFAULT_PORT), DEFAULT_PORT, 1024, 65535),
                  tuners=_int(settings.value(K_TUNERS, 0), 0, 0, MAX_TUNERS),
                  sources=parse_sources(settings.value(K_SOURCES, "")),
                  token=token, admin=admin, device_id=device,
                  adult=read_flag(settings.value(K_ADULT, False)),
                  open=open_mode(settings),
                  open_hosts=tuple(parse_open_hosts(settings.value(K_OPEN_HOSTS, ""))[0]))


def changes_from_page(body: dict, known: list[str]) -> dict[str, str]:
    """What the browser page asks to change, as settings - only what is
    valid (a port, a number of tuners, sources this app has)."""
    out: dict[str, str] = {}
    if "port" in body:
        port = _int(body.get("port"), -1, 1024, 65535)
        if port < 0:
            raise ValueError("the port is a number from 1024 to 65535")
        out[K_PORT] = str(port)
    if "tuners" in body:
        n = _int(body.get("tuners"), -1, 0, MAX_TUNERS)
        if n < 0:
            raise ValueError(f"the tuners are a number from 0 to {MAX_TUNERS}")
        out[K_TUNERS] = str(n)
    if "sources" in body:
        src = body.get("sources")
        if not isinstance(src, dict):
            raise ValueError('"sources" is {source id: [category ids]}')
        kept = {str(k): [str(c) for c in v][:5000] for k, v in src.items()
                if str(k) in known and isinstance(v, list)}
        out[K_SOURCES] = json.dumps(kept, sort_keys=True) if kept else ""
    if "adult" in body:
        if not isinstance(body.get("adult"), bool):
            raise ValueError('"adult" is true or false')
        out[K_ADULT] = "true" if body["adult"] else "false"
    return out


def launch_command() -> str:
    """The command that starts the server without a window, as this copy
    of the app runs."""
    import os
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return f'"{appimage}" --server'
    # In the Flatpak, "dopeiptv" is the sandbox's own command, not one the
    # computer's shell or a systemd service has.
    flatpak = os.environ.get("FLATPAK_ID", "")
    if re.fullmatch(r"[A-Za-z0-9_.-]+", flatpak):
        return f"flatpak run {flatpak} --server"
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --server'
    import shutil
    if shutil.which("dopeiptv"):
        return "dopeiptv --server"
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return f'"{sys.executable}" "{os.path.join(root, "dopeiptv.py")}" --server'


# -- what the server asks of the app -------------------------------------------------------

class Host(Protocol):
    def sources(self) -> list[dict]:
        """The household's sources, the active one first."""
    def filters(self, pid: str) -> tuple[set[str], set[str], dict[str, int]]:
        """(excluded category ids - hidden or locked -, channels hidden by
        hand, the channel numbers of the app's own) of source *pid*."""
    def app_use(self, pid: str) -> tuple[int, list[str]]:
        """The app's own connections to *pid* now (player, recordings,
        multiview, casts - and another device's as the provider last said,
        for a host with ``counts_others``) and the addresses they play."""
    def app_session(self, pid: str, sid: str) -> DvrSession | None:
        """The app's own buffer session of a channel, while it plays it."""
    def pool(self) -> DvrPool:
        """The rewind buffers' pool the restream reads from."""
    def save(self, changes: dict[str, str]) -> Config:
        """Settings the browser page changed, stored; the settings now."""
    def page(self) -> tuple[bytes, str]:
        """The browser page and its Content-Security-Policy."""
    # And, where a host has them: ``kind`` (HOLDER_WINDOW, HOLDER_HEADLESS:
    # what MARK says), ``counts_others``, ``guides_kept``.


@dataclass(frozen=True)
class LineupChannel:
    number: str
    name: str
    source: str
    stream: str
    epg_id: str = ""
    logo: str = ""
    group: str = ""


class HomeServer:
    def __init__(self, host: Host, config: Config, bind: str = "0.0.0.0",
                 discovery_port: int = hdhr.DISCOVER_PORT,
                 make_client: Callable[[dict], Any] | None = None,
                 guide_path: Callable[[str], str] | None = None) -> None:
        self.host, self.config = host, config
        self.bind = bind
        self.discovery_port = discovery_port
        self.port = config.port
        self._guide_path = guide_path
        self.share = Share(self.served, make_client=make_client, guide_path=guide_path)
        #: More sources whose guides are kept fresh than those served: the
        #: ones the server's recorder plans series recordings from
        #: (services/server_recorder.py); None: none.
        self.also_guides: Callable[[], list[dict]] | None = None
        #: The served sources' guides kept fresh; the window keeps its active
        #: source's itself (a host's ``guides_kept``).
        self.guides = GuideKeeper(self._guide_sources, self.share._client,
                                  kept=getattr(host, "guides_kept", None), guide_path=guide_path)
        self.limiter = RateLimiter()
        #: How often one address may ask what needs no token.
        self.tuner_rate = RateLimiter(rate=TUNER_RATE, burst=TUNER_BURST)
        #: Who may use the endpoints that need no token.
        self.local = home_net.LocalNetworks()
        self.ledger = connections.Ledger()
        self._lineup: tuple[float, list[LineupChannel]] | None = None
        self._lineup_lock = threading.Lock()
        self._authed: set[str] = set()
        self._refused_names: set[str] = set()
        self._refused_addresses: set[str] = set()
        self._refused_lock = threading.Lock()
        #: The addresses that used the tuner without its token this run
        #: (``note_open_use``): who "Close it" keeps letting in.
        self._open_seen: dict[str, None] = {}
        self.restream = Restreamer(host.pool, self.limit, self.total, others=self._others,
                                   app_session=host.app_session, ingest_for=self._ingest_for,
                                   check=self._check)
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None
        self._udp: hdhr.Responder | None = None
        self.error = ""
        #: The last move found its port in use (port_taken).
        self.taken = False
        #: What MARK says in every answer.
        self.mark = str(getattr(host, "kind", "") or "server")

    def refused_name(self, host: str | None) -> bool:
        """A tuner endpoint asked under a name that is not the home's
        (``tuner_host_ok``): logged once per name - a media server set up
        with that name otherwise loses its tuner with nothing said. True
        when it was logged."""
        name = (host or "").strip().lower()[:120]
        with self._refused_lock:
            if name in self._refused_names or len(self._refused_names) >= REFUSED_NAMES_MAX:
                return False
            self._refused_names.add(name)
        log.info("server: the tuner was asked as %r, not a home network's name, and refused - "
                 "a device asks it by this computer's address or a name under %s",
                 name, ", ".join(HOME_SUFFIXES))
        return True

    def refused_address(self, address: str) -> bool:
        """A tuner endpoint asked from an address outside this computer's own
        home networks (``home_net.LocalNetworks``): logged once per address,
        with how to let it in - a media server reaching the tuner over a VPN
        or Tailscale otherwise loses it with nothing said. True when logged."""
        who = (address or "").strip()[:64]
        with self._refused_lock:
            if who in self._refused_addresses or len(self._refused_addresses) >= REFUSED_NAMES_MAX:
                return False
            self._refused_addresses.add(who)
        if self.config.open == OPEN_HOME:
            log.info("server: the tuner refused %s - not on one of this computer's own home "
                     "networks (a VPN's, Tailscale's or a provider's shared range are not); to "
                     "let it in, add it under Nerd > Network > Other networks", who)
        else:
            log.info("server: the tuner refused %s without its token - give that device the "
                     "tuner's address with the token, or let it in under Nerd > Remote and "
                     "automation > Server mode", who)
        return True

    # -- on and off ---------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        """Listen; OSError when the port is taken (the caller says so)."""
        httpd = _Server((self.bind, self.config.port), _make_handler(self))
        self.port = httpd.server_address[1]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="home-server",
                                        daemon=True)
        self._thread.start()
        if self.discovery_port:
            udp = hdhr.Responder(self._discovery_answer, self.discovery_port,
                                 allowed=self._may_discover)
            if udp.start():
                self._udp = udp
        threading.Thread(target=self._learn_limits, name="home-server-limits",
                         daemon=True).start()
        self.guides.start()
        log.info("server: listening on port %d (%d tuner(s))", self.port, self.total())

    def move(self, port: int) -> bool:
        """Listen on *port* from now on (the page changed it); the streams
        that play go on."""
        if port == self.port and self._httpd is not None:
            return True
        try:
            httpd = _Server((self.bind, port), _make_handler(self))
        except OSError as e:
            self.error = f"{type(e).__name__}: {e}"
            self.taken = port_taken(e)
            log.warning("server: cannot move to port %d (%s) - it stays on %d", port, e, self.port)
            return False
        self.error, self.taken = "", False
        old, self._httpd = self._httpd, httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, name="home-server",
                                        daemon=True)
        self._thread.start()
        if old is not None:
            old.shutdown()
            old.server_close()
        log.info("server: moved to port %d", self.port)
        return True

    def stop(self) -> None:
        self.guides.stop()
        self.restream.close()
        udp, self._udp = self._udp, None
        if udp is not None:
            udp.stop()
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            log.info("server: stopped")

    # -- what it serves --------------------------------------------------------------------
    def served(self) -> list[dict]:
        """The sources served: those chosen, else the active one."""
        everything = [p for p in (self.host.sources() or []) if p.get("id") and p.get("kind") != "local"]
        chosen = self.config.sources
        if chosen:
            return [p for p in everything if str(p["id"]) in chosen]
        return everything[:1]

    def source(self, pid: str) -> dict | None:
        return next((p for p in self.served() if str(p["id"]) == pid), None)

    def _guide_sources(self) -> list[dict]:
        """The sources whose guides are kept fresh: those served, and those
        the recorder plans from (``also_guides``)."""
        out = list(self.served())
        more = self.also_guides
        if more is not None:
            try:
                seen = {str(p.get("id")) for p in out}
                out += [p for p in more() if str(p.get("id")) not in seen]
            except Exception as e:
                log.debug("server: the recorder's sources not read: %s", e)
        return out

    def allowed_categories(self, pl: dict) -> list[tuple[str, str]]:
        """(id, name) of the categories of *pl* that may be served at all."""
        from ..core.profiles import adult_category
        _rows, cats = self.share.channels(pl)
        excluded = self.host.filters(str(pl["id"]))[0]
        return [(cid, name) for cid, name in cats.items()
                if cid not in excluded and (self.config.adult or not adult_category(name))]

    def lineup(self) -> list[LineupChannel]:
        """Every channel served, numbered: the app's own numbers where it
        has them, else the source's; a number taken or missing gets the
        next one free after the highest."""
        with self._lineup_lock:
            held = self._lineup
            if held is not None and time.monotonic() - held[0] < LINEUP_TTL:
                return held[1]
        picked: list[tuple[str | None, dict, dict, str]] = []
        for pl in self.served():
            pid = str(pl["id"])
            try:
                rows, cats = self.share.channels(pl)
            except Exception as e:
                log.info("server: %s left out (%s)", pl.get("name"), e)
                continue
            excluded, hidden, numbers = self.host.filters(pid)
            wanted = set(self.config.sources.get(pid) or [])
            for r in rows:
                cid = str(r.get("category_id"))
                sid = str(r.get("stream_id"))
                if kept_out(r, cats, excluded, hidden, adult=self.config.adult) \
                        or (wanted and cid not in wanted):
                    continue
                own = numbers.get(sid)
                num = str(own if own is not None else (r.get("_num_own", r.get("num")) or "")).strip()
                picked.append((num if re.fullmatch(r"[0-9]{1,6}", num) else None, r, pl, cats.get(cid, "")))
        taken: set[str] = set()
        top = max([int(n) for n, *_ in picked if n is not None] or [0])
        out: list[LineupChannel] = []
        for given, r, pl, group in picked:
            num = given if given is not None and given not in taken else ""
            if not num:
                top += 1
                num = str(top)
            taken.add(num)
            out.append(LineupChannel(num, str(r.get("name") or num), str(pl["id"]),
                                     str(r.get("stream_id")), str(r.get("epg_channel_id") or ""),
                                     # A logo with a box's login or a token in it
                                     # is left out: devices never get either.
                                     public_logo(r.get("stream_icon")), group))
        with self._lineup_lock:
            self._lineup = (time.monotonic(), out)
        return out

    def channel(self, source: str, stream: str) -> LineupChannel | None:
        return next((c for c in self.lineup() if c.source == source and c.stream == stream), None)

    def by_number(self, number: str) -> LineupChannel | None:
        return next((c for c in self.lineup() if c.number == number), None)

    def refresh(self, config: Config | None = None) -> None:
        """The settings or the sources changed: the lineup is made again."""
        if config is not None:
            self.config = config
        with self._lineup_lock:
            self._lineup = None

    # -- tuners ------------------------------------------------------------------------------
    def _client_of(self, pid: str) -> Any:
        pl = self.source(pid)
        return self.share._client(pl) if pl is not None else None

    def known_limit(self, pid: str) -> int | None:
        """What the account (or the box) says it allows, when it says."""
        client = self._client_of(pid) if pid in self._authed else None
        n = getattr(client, "max_connections", None)
        if not n:
            n = (getattr(client, "box_info", None) or {}).get("tuners") if client is not None else None
        n = n or self.ledger.limit(pid)
        try:
            return int(n) if n else None
        except (TypeError, ValueError):
            return None

    def limit(self, pid: str) -> int:
        if self.config.tuners:
            return self.config.tuners
        return self.known_limit(pid) or 1

    def total(self) -> int:
        if self.config.tuners:
            return self.config.tuners
        return max(1, sum(self.limit(str(p["id"])) for p in self.served()))

    def auto_tuners(self) -> int:
        return max(1, sum(self.known_limit(str(p["id"])) or 1 for p in self.served()))

    def _learn_limits(self) -> None:
        """(a thread of its own) What each served account allows: Xtream says
        it when signed in; a box when asked."""
        for pl in self.served():
            pid = str(pl["id"])
            try:
                client = self.share._client(pl)
                if isinstance(getattr(client, "box_info", None), dict):
                    if not client.box_info:
                        client.authenticate()       # a box says its tuners when asked
                elif hasattr(client, "account_info"):
                    info = client.account_info()
                    if getattr(client, "max_connections", None) is None and info:
                        limit, _active = connections.numbers((info or {}).get("user_info"))
                        client.max_connections = limit
                seen = getattr(client, "account_seen", None)
                if isinstance(seen, dict) and not getattr(self.host, "counts_others", False):
                    own = self.host.app_use(pid)[0] + self.restream.in_use(pid)
                    self.ledger.report(pid, seen.get("max"), seen.get("active"), own,
                                       at=float(seen.get("at") or time.monotonic()))
            except Exception as e:
                log.debug("server: %s's limit not known (%s)", pl.get("name"), e)
            self._authed.add(pid)
        log.info("server: %d tuner(s) - %s", self.total(),
                 ", ".join(f"{p.get('name')}: {self.limit(str(p['id']))}" for p in self.served()) or "-")

    def _others(self, pid: str) -> int:
        """Connections to *pid* that are not the server's: the app's own and
        (as the provider last said) another device's - the window counts
        those itself (``counts_others``), the headless server here."""
        own = self.host.app_use(pid)[0]
        return own if getattr(self.host, "counts_others", False) else own + self.ledger.others(pid)

    def _ingest_for(self, pid: str) -> Any:
        """Each channel opened with its own source's options (user agent,
        headers, proxy, route) - not the window's active source's."""
        pl = self.source(pid)
        return self._ingest_of(pl) if pl is not None else None

    def _ingest_of(self, pl: dict) -> Any:
        """The buffer's connection for a channel of source *pl* - served or
        not: a recording's (services/server_recorder.py)."""
        from ..core.dvr_ingest import Ingest, opener_for
        src = dict(pl.get("options") or {}, _kind=pl.get("kind") or "xtream",
                   _server=str(pl.get("server") or ""))
        opener = opener_for(src)

        def factory(url: str, store: Any) -> Ingest:
            ff = _ffmpeg() if is_hls(url) else None
            return Ingest(url, store, opener=ffmpeg_opener(ff, src) if ff else opener)
        return factory

    def _check(self, pid: str, sid: str, url: str, server_urls: list[str]) -> Busy | None:
        """Before a new connection: the VPN guard, and a box's tuners (an
        Enigma2 box: would it cut off a recording or what its TV shows?)."""
        try:
            from ..core import nerd, tuners
            pl = self.source(pid) or {}
            if nerd.blocks_stream(url, pl.get("kind")):
                return Busy(VPN, "no VPN up (Nerd's VPN guard)")
            if is_hls(url) and not _ffmpeg():
                return Busy(NO_DATA, "an adaptive (HLS) channel needs ffmpeg to be restreamed")
            scheme = urlsplit(url).scheme.lower()
            if scheme not in ("http", "https"):
                # A SAT>IP server's RTSP, a multicast udp:// - which the
                # buffer's connection cannot read: said now, not after five
                # tries and some sixteen seconds.
                return Busy(NO_DATA, f"a {scheme or 'local'} address is not restreamed, only HTTP")
            client = self._client_of(pid)
            key = tuners.stream_key(url)
            if client is None or key is None or not hasattr(client, "tuner_state"):
                return None
            state = client.tuner_state()
            app_urls = self.host.app_use(pid)[1]
            own = [tuners.Tune("server", "", tuners.stream_key(u)) for u in server_urls]
            own += [tuners.Tune("player", "", tuners.stream_key(u)) for u in app_urls]
            v = tuners.verdict(state, key, [t for t in own if t.key])
            if v is not None and v.kind == tuners.CUTS:
                return Busy(BOX, "it would cut off a recording or what the box's TV shows")
        except Exception as e:
            log.debug("server: the box not asked (%s)", e)
        return None

    # -- the addresses -----------------------------------------------------------------------
    def base(self, host_header: str | None = None, client: str = "") -> str:
        """The server's address as *client* reaches it: under the name it
        asked by when that is a home network's (home_net.host_for_addresses),
        else by this computer's address on the way to it - never under
        whatever Host a request carries, since the playlist's addresses made
        from it hold the token."""
        named = home_net.host_for_addresses(host_header)
        if named:
            return f"http://{named}"
        ip = hdhr.address_towards(client) if client else (lan_addresses() or ["127.0.0.1"])[0]
        return f"http://{ip}:{self.port}"

    def tuner_address(self, base: str) -> str:
        """The tuner's address for a media server: under its token unless
        devices are let in without it (K_OPEN)."""
        if self.config.open == OPEN_OFF:
            return f"{base}/t/{self.config.token}"
        return base

    def addresses(self, base: str) -> dict[str, str]:
        q = f"?token={quote(self.config.token)}"
        page = f"?token={quote(self.config.admin or self.config.token)}"
        return {"tuner": self.tuner_address(base), "tuner_tokened": f"{base}/t/{self.config.token}",
                "playlist": f"{base}/playlist.m3u{q}",
                "guide": f"{base}/xmltv.xml{q}", "page": f"{base}/{page}"}

    def open_to(self, ip: str, own: str = "") -> bool:
        """May *ip* use the tuner without its token (K_OPEN)? Nobody by
        default; the devices listed; or, as before 1.6.9, every device on
        this computer's own networks."""
        mode = self.config.open
        if mode == OPEN_HOME:
            return self.local.allows(ip, own)
        if mode == OPEN_DEVICES:
            norm = _plain_address(ip)
            return norm is not None and norm in self.config.open_hosts
        return False

    def note_open_use(self, ip: str) -> None:
        """*ip* used the tuner without its token (it was let in): kept for
        this run, at most OPEN_HOSTS_MAX, as K_OPEN_HOSTS would hold it."""
        norm = _plain_address(ip)
        if norm is None:
            return
        with self._refused_lock:
            if norm not in self._open_seen and len(self._open_seen) < OPEN_HOSTS_MAX:
                self._open_seen[norm] = None

    def open_seen(self) -> list[str]:
        """The addresses that used the tuner without its token this run."""
        with self._refused_lock:
            return list(self._open_seen)

    def _may_discover(self, ip: str) -> bool:
        # The UDP search gets the tuner's address without its token: only
        # for a device that may use it so.
        return self.open_to(ip)

    def _discovery_answer(self, ip: str) -> tuple[str, int, str] | None:
        if not self.running:
            return None
        return self.config.device_id, self.total(), f"http://{hdhr.address_towards(ip)}:{self.port}"

    # -- what it says ---------------------------------------------------------------------------
    def status(self, base: str) -> dict[str, Any]:
        st = self.restream.status()
        names = {str(p["id"]): str(p.get("name") or "") for p in self.served()}
        for ch in st["channels"]:
            ch["source_name"] = names.get(ch["source"], "")
        st["addresses"] = self.addresses(base)
        # What the server records (a host with a recorder: dopeiptv --server).
        recordings = getattr(self.host, "recordings", None)
        if callable(recordings):
            try:
                got = recordings()
                if got is not None:
                    st["recordings"] = got
            except Exception as e:
                log.debug("server: the recordings not said: %s", e)
        return st

    def settings_view(self) -> dict[str, Any]:
        chosen = self.config.sources
        everything = [p for p in (self.host.sources() or []) if p.get("id") and p.get("kind") != "local"]
        served = {str(p["id"]) for p in self.served()}
        return {"port": self.port, "tuners": self.config.tuners, "auto_tuners": self.auto_tuners(),
                "adult": self.config.adult, "command": launch_command(),
                # A source without a name is told by its address - without
                # the login an M3U's address carries in it.
                "sources": [{"id": str(p["id"]),
                             "name": str(p.get("name") or redact_url(p.get("server") or "")),
                             "kind": str(p.get("kind") or "xtream"), "served": str(p["id"]) in served,
                             "categories": (chosen.get(str(p["id"])) or None)} for p in everything]}

    def m3u(self, base: str) -> str:
        q = f"?token={quote(self.config.token)}"
        out = [f'#EXTM3U url-tvg="{base}/xmltv.xml{q}" x-tvg-url="{base}/xmltv.xml{q}"']
        for ch in self.lineup():
            name = _attr(ch.name)
            attrs = [f'tvg-id="{_attr(ch.number)}"', f'tvg-name="{name}"', f'tvg-chno="{_attr(ch.number)}"']
            if ch.logo:
                attrs.append(f'tvg-logo="{_attr(ch.logo)}"')
            if ch.group:
                attrs.append(f'group-title="{_attr(ch.group)}"')
            out.append(f"#EXTINF:-1 {' '.join(attrs)},{name}")
            out.append(f"{base}/stream/{quote(ch.source, safe='')}/{quote(ch.stream, safe='')}{q}")
        return "\n".join(out) + "\n"

    def guide(self) -> Iterator[bytes]:
        if self._guide_path is None:
            from ..providers.epg import epg_cache_path
            self._guide_path = epg_cache_path
        chans = [{"number": c.number, "name": c.name, "source": c.source, "epg_id": c.epg_id,
                  "logo": c.logo} for c in self.lineup()]
        guides = [(str(p["id"]), self._guide_path(str(p["id"]))) for p in self.served()]
        return lineup_guide(guides, chans)

    def stream_url(self, ch: LineupChannel) -> str:
        pl = self.source(ch.source)
        if pl is None:
            return ""
        return str(self.share._client(pl).live_url(ch.stream, "ts") or "")

    def save(self, changes: dict[str, str]) -> None:
        """What the browser page changed: stored by the app, in force here -
        a new port once this answer is out."""
        cfg = self.host.save(changes)
        self.refresh(cfg)
        if K_PORT in changes and cfg.port != self.port:
            threading.Timer(0.5, self.move, args=(cfg.port,)).start()


def _attr(value: Any) -> str:
    return re.sub(r"[\s\"]+", " ", str(value or "")).strip()


def _ffmpeg() -> str | None:
    """The app's ffmpeg (a packaged build's own, else the system's)."""
    from ..core.recording import RecordingManager
    return RecordingManager.ffmpeg()


#: The name endings a home network's own names have (core/home_net.py).
HOME_SUFFIXES = home_net.HOME_SUFFIXES
#: Refused names logged (``refused_name``), so a flood of them stays small.
REFUSED_NAMES_MAX = 64


def tuner_host_ok(host: str | None) -> bool:
    """Whether the ``Host`` a tuner endpoint was asked under is a name of
    the home network: none (an HTTP/1.0 client), an IP address, localhost,
    this computer's own name, a name without a dot, or a home suffix
    (home_net.home_host). The tuner's endpoints need no token, so a web
    page whose own name a DNS rebinding pointed at this computer must not
    reach them: its Host is its own public name. Plex, Jellyfin, Channels
    and the rest ask by address."""
    return home_gate.host_ok(host)


# The gate's (core/home_gate.py), by the names the tests reach them by.
_loopback = home_gate.this_computer
_own = home_gate.own_address


class _Server(ConnectionLimitMixin, ExclusiveBindMixin, ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError,
                            ConnectionAbortedError)):
            return
        log.info("server: a request failed (%s)", exc)


def _make_handler(server: HomeServer) -> type[BaseHTTPRequestHandler]:
    class Handler(IdleTimeoutMixin, BaseHTTPRequestHandler):
        server_version = "dopeIPTV"
        sys_version = ""
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):     # noqa: A003 - stdlib hook
            pass

        # -- answers -----------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str,
                  extra: dict[str, str] | None = None) -> None:
            # The answer has its time too (ANSWER_TIMEOUT): a client that
            # asked and then reads nothing no longer holds it for good.
            self._idle_limit(ANSWER_TIMEOUT)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in {**SAFE_HEADERS, MARK: server.mark, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _text(self, code: int, text: str, extra: dict[str, str] | None = None) -> None:
            self._send(code, (text + "\n").encode("utf-8"), "text/plain; charset=utf-8", extra)

        # -- the request ---------------------------------------------------------
        def _authorized(self, given: str, admin: bool = False) -> bool:
            """*given* opens what is asked: the page and its api/ only with
            the admin token; the playlist, the guide and the streams with
            either."""
            cfg = server.config
            return home_gate.token_ok(given, *([cfg.admin or cfg.token] if admin
                                              else [cfg.token, cfg.admin]))

        def _base(self) -> str:
            return server.base(self.headers.get("Host"), str(self.client_address[0]))

        def _handle(self, method: str) -> None:
            who = str(self.client_address[0])
            if not home_gate.at_home(who, _own(self)):
                self._text(403, "only the home network")
                return
            lim = server.limiter
            parts = urlsplit(self.path)
            path = parts.path.rstrip("/") or "/"
            query = parse_qs(parts.query, keep_blank_values=False)
            reading = method in ("GET", "HEAD")
            try:
                tokened = _TOKENED.match(path)
                if tokened:
                    # The tuner under its token: what a media server is
                    # given unless devices are let in without it.
                    inner = tokened.group(2) or "/discover.json"
                    if not (inner in HDHR_PATHS or _AUTO.match(inner)):
                        self._text(404, "not found")
                        return
                    if lim.blocked(who):
                        self._text(429, "too many wrong tokens - try again later",
                                   {"Retry-After": "60"})
                        return
                    if cross_site(self.headers):
                        self._text(403, "not for a web page")
                        return
                    if not home_gate.token_ok(tokened.group(1), server.config.token):
                        home_gate.wrong_token(lim, who, self.headers, here=_loopback(who),
                                              said="server: an address on the network kept "
                                                   "guessing the tuner's token")
                        time.sleep(0.25)
                        self._text(401, "the tuner's address has the wrong token")
                        return
                    self._hdhomerun(inner, method, f"{self._base()}/t/{server.config.token}")
                    return
                if path in HDHR_PATHS or _AUTO.match(path):
                    # No token here, so no lock-out either: a shut-out
                    # address still has its tuner.
                    if not server.open_to(who, _own(self)):
                        # No token here: only the devices let in without it
                        # (Settings > Nerd > Remote and automation).
                        server.refused_address(who)
                        if server.config.open == OPEN_HOME:
                            self._text(403, "only this computer's own network")
                        else:
                            self._text(401, "the tuner's address needs its token (dopeIPTV > "
                                            "Settings > Nerd > Remote and automation)")
                        return
                    if cross_site(self.headers):
                        # A web page in a browser here (an <img>, a fetch)
                        # would open the provider's connection; no tuner
                        # client sends an Origin or Sec-Fetch-Site.
                        self._text(403, "not for a web page")
                        return
                    if not tuner_host_ok(self.headers.get("Host")):
                        # Not a home name: a web page's own, rebound here.
                        server.refused_name(self.headers.get("Host"))
                        self._text(403, "ask the tuner by this computer's address")
                        return
                    if not server.tuner_rate.allow(who):
                        # No token to get wrong here: how often it asks
                        # is all there is to hold an address to.
                        self._text(429, "asked too often - try again in a moment",
                                   {"Retry-After": "5"})
                        return
                    server.note_open_use(who)
                    self._hdhomerun(path, method, self._base())
                    return
                if lim.blocked(who):
                    self._text(429, "too many wrong tokens - try again later", {"Retry-After": "60"})
                    return
                given = home_gate.given_token(self.headers, query)
                admin = path == "/" or path.startswith("/api/")
                if not self._authorized(given, admin):
                    # Only a try at the token counts: a browser asks for
                    # /favicon.ico by itself. Never this computer's nor a
                    # web page's: a page in a browser here would shut out
                    # the person's own browser, or a phone that visits it.
                    if given:
                        home_gate.wrong_token(lim, who, self.headers, here=_loopback(who),
                                              said="server: an address on the network kept "
                                                   "guessing the token")
                    time.sleep(0.25)
                    self._text(401, "the address needs its token (dopeIPTV > Settings > Nerd > "
                                    "Remote and automation)")
                    return
                if path == "/" and reading:
                    html, csp = server.host.page()
                    self._send(200, html, "text/html; charset=utf-8",
                               {"Content-Security-Policy": csp})
                elif path.startswith("/api/"):
                    self._api(path[5:], method, query)
                elif path == "/playlist.m3u" and reading:
                    self._send(200, server.m3u(self._base()).encode("utf-8"),
                               "audio/x-mpegurl; charset=utf-8")
                elif path in ("/xmltv.xml", "/guide.xml") and reading:
                    self._chunks("application/xml; charset=utf-8", server.guide())
                elif path.startswith("/stream/") and reading:
                    bits = path.split("/")
                    ch = server.channel(unquote(bits[2]), unquote(bits[3])) if len(bits) == 4 else None
                    if ch is None:
                        self._text(404, "no such channel")
                    else:
                        self._stream(ch, method, hdhomerun=False)
                else:
                    self._text(404, "not found")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except TimeoutError:
                self._json(503, {"error": "the app is busy - try again"})
            except Exception as e:
                log.warning("server: %s failed: %s", path.split("/")[1] or "page", e)
                try:
                    self._json(500, {"error": type(e).__name__})
                except OSError:
                    pass

        def _hdhomerun(self, path: str, method: str, base: str) -> None:
            cfg = server.config
            if path == "/lineup.post":
                self._text(200, "")              # a scan: the lineup is always current
            elif method not in ("GET", "HEAD"):
                self._text(405, "use GET")
            elif path == "/discover.json":
                self._json(200, hdhr.discover(cfg.device_id, server.total(), base))
            elif path == "/lineup.json":
                self._json(200, hdhr.lineup([{"number": c.number, "name": c.name}
                                             for c in server.lineup()], base))
            elif path == "/lineup_status.json":
                self._json(200, hdhr.lineup_status())
            elif path == "/device.xml":
                self._send(200, hdhr.device_xml(cfg.device_id, base).encode("utf-8"),
                           "application/xml; charset=utf-8")
            else:
                m = _AUTO.match(path)
                ch = server.by_number(m.group(1)) if m else None
                if ch is None:
                    self._text(404, "no such channel", {"X-HDHomeRun-Error": "804 Invalid Channel"})
                else:
                    self._stream(ch, method, hdhomerun=True)

        def _api(self, action: str, method: str, query: dict[str, list[str]]) -> None:
            if action == "status" and method in ("GET", "HEAD"):
                self._json(200, server.status(self._base()))
            elif action == "settings" and method in ("GET", "HEAD"):
                self._json(200, server.settings_view())
            elif action == "settings" and method == "POST":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if not 0 < length <= MAX_BODY:
                    raise ValueError("the body is a JSON object")
                self._idle_limit(ANSWER_TIMEOUT)       # a body that never comes
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("the body is a JSON object")
                known = [str(p.get("id")) for p in server.host.sources() or []]
                changes = changes_from_page(body, known)
                server.save(changes)
                self._json(200, {"ok": True, "port": int(changes.get(K_PORT) or server.port)})
            elif action == "categories" and method in ("GET", "HEAD"):
                pl = next((p for p in server.host.sources() or []
                           if str(p.get("id")) == (query.get("source") or [""])[0]), None)
                if pl is None:
                    self._json(404, {"error": "no such source"})
                    return
                cats = server.allowed_categories(pl)
                self._json(200, {"categories": [{"id": c, "name": n} for c, n in cats]})
            else:
                self._json(404, {"error": "not found"})

        def _chunks(self, ctype: str, chunks: Iterator[bytes]) -> None:
            self._idle_limit(ANSWER_TIMEOUT)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            for k, v in {**SAFE_HEADERS, MARK: server.mark}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command == "HEAD":
                return
            for chunk in chunks:
                if chunk:
                    self.wfile.write(chunk)

        def _stream(self, ch: LineupChannel, method: str, hdhomerun: bool) -> None:
            if method == "HEAD":
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.end_headers()
                return
            url = server.stream_url(ch)
            if not url:
                self._text(502, "the source gave no address for this channel")
                return
            who = str(self.client_address[0])
            try:
                v = server.restream.watch(ch.source, ch.stream, url, who, ch.name)
            except Busy as b:
                extra = {"Retry-After": "10"}
                if hdhomerun:
                    extra["X-HDHomeRun-Error"] = ("805 All Tuners In Use" if b.reason in (TUNERS, BOX)
                                                  else "807 No Video Data")
                log.info("server: %s for %s refused - %s", ch.name, who, b.detail)
                self._text(503, b.detail, extra)
                return
            gen: Any = server.restream.stream(v)
            try:
                first = next(gen, None)
                if first is None:
                    extra = {"X-HDHomeRun-Error": "807 No Video Data"} if hdhomerun else {}
                    self._text(503, v.why or "no picture from the provider", extra)
                    return
                log.info("server: %s to %s (%s)", ch.name, who, redact_url(url))
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.connection.settimeout(STREAM_WRITE_TIMEOUT)
                except OSError:
                    pass
                self.wfile.write(first)
                for chunk in gen:
                    self.wfile.write(chunk)
            except (OSError, TimeoutError):
                pass                    # the client went away
            finally:
                gen.close()

        def do_GET(self):      # noqa: N802 - stdlib hook
            self._handle("GET")

        def do_HEAD(self):     # noqa: N802
            self._handle("HEAD")

        def do_POST(self):     # noqa: N802
            self._handle("POST")

        def do_PUT(self):      # noqa: N802
            self._text(405, "use GET or POST")

        do_DELETE = do_PUT
        do_PATCH = do_PUT

    return Handler


# -- a port that is taken --------------------------------------------------------------------

#: Seconds the holder of a port is given for each step of its answer.
HOLDER_TIMEOUT = 1.5


def port_taken(e: BaseException) -> bool:
    """Whether *e* says the port is in use (EADDRINUSE, Windows'
    WSAEADDRINUSE) - not any other reason a port does not open."""
    if not isinstance(e, OSError):
        return False
    return (e.errno in (errno.EADDRINUSE, _WSAEADDRINUSE)
            or getattr(e, "winerror", None) == _WSAEADDRINUSE)


def avoided_ports(settings: Any) -> set[int]:
    """The app's other servers' ports - the remote API's and the phone
    remote's, their defaults and as set: never offered for this one."""
    from ..core import phone_remote, remote_api
    out = {remote_api.DEFAULT_PORT, phone_remote.DEFAULT_PORT}
    for key in (remote_api.PORT_KEY, phone_remote.PORT_KEY):
        n = _int(settings.value(key, 0), 0, 1, 65535)
        if n:
            out.add(n)
    return out


def bindable(port: int, bind: str = "0.0.0.0") -> bool:
    """Whether the server could listen on *port* now, bound as it binds
    (_Server: exclusively on Windows, SO_REUSEADDR elsewhere)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if excl is not None:
                s.setsockopt(socket.SOL_SOCKET, excl, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((bind, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def next_free_port(port: int, avoid: Iterable[int] = (), bind: str = "0.0.0.0",
                   tries: int = 100) -> int | None:
    """The first port after *port* the server could listen on now, each
    tried by binding it; the app's other servers' (*avoid*) left out. None
    when none of the next *tries* is free."""
    skip = set(avoid)
    for p in range(port + 1, min(65535, port + tries) + 1):
        if p not in skip and bindable(p, bind):
            return p
    return None


def port_holder(port: int, host: str = "127.0.0.1", timeout: float = HOLDER_TIMEOUT) -> str:
    """Who listens on *port* on this computer (HOLDER_*): its
    ``discover.json`` asked on loopback - no token, nothing else sent.
    dopeIPTV's server says so (MARK); one before 1.6.1 by its name, in its
    Server header and as the manufacturer in its discover.json. No answer,
    or any other answer: another program."""
    import http.client
    deadline = time.monotonic() + 2 * timeout
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/discover.json", headers={"Accept": "application/json"})
        resp = conn.getresponse()
        mark = (resp.getheader(MARK) or "").strip().lower()
        if mark:
            return mark if mark in (HOLDER_WINDOW, HOLDER_HEADLESS) else HOLDER_OURS
        name = (resp.getheader("Server") or "").split("/")[0].strip()
        if resp.status != 200 or name != hdhr.MANUFACTURER:
            return HOLDER_OTHER
        body = b""
        while len(body) < MAX_BODY and time.monotonic() < deadline:
            chunk = resp.read(min(4096, MAX_BODY - len(body)))
            if not chunk:
                break
            body += chunk
        data = json.loads(body.decode("utf-8", "replace"))
    except (OSError, http.client.HTTPException, ValueError):
        return HOLDER_OTHER
    finally:
        conn.close()
    ours = isinstance(data, dict) and data.get("Manufacturer") == hdhr.MANUFACTURER
    return HOLDER_OURS if ours else HOLDER_OTHER


@dataclass(frozen=True)
class Taken:
    """A port found in use: who holds it (HOLDER_*) and the next port free
    (None: none near it)."""
    port: int
    holder: str
    free: int | None = None

    @property
    def ours(self) -> bool:
        return self.holder != HOLDER_OTHER

    @property
    def key(self) -> str:
        """What the window says of it."""
        if self.holder == HOLDER_WINDOW:
            return "nerd_ck_server_ours_app"
        return "nerd_ck_server_ours" if self.ours else "nerd_ck_server_taken"


def look_at_port(port: int, avoid: Iterable[int] = (), bind: str = "0.0.0.0",
                 timeout: float = HOLDER_TIMEOUT) -> Taken:
    """(off the window's thread) *port*, found in use: who holds it, and the
    next port free."""
    return Taken(port, port_holder(port, timeout=timeout), next_free_port(port, avoid, bind))


# -- the Nerd page's line ------------------------------------------------------------------

def check_server(on: bool, running: bool, port: int, error: str, status: dict | None,
                 addresses: dict[str, str] | None, taken: Taken | None = None) -> Check:
    """How the server stands, for the line under its switch. *taken*: the
    port it was to listen on is in use (it runs on *port* still when a move
    found it so); the next free one, in the params, is offered there."""
    if not on:
        return Check(WARN, "nerd_ck_server_off", {})
    if taken is not None and (not running or taken.port != port):
        return Check(WARN if running else FAIL, taken.key,
                     {"port": taken.port, "free": taken.free}, error)
    if not running:
        return Check(FAIL, "nerd_ck_server_down", {"port": port}, error)
    st = status or {}
    lines = [f"{k}: {v}" for k, v in (addresses or {}).items() if k != "page"]
    return Check(OK, "nerd_ck_server_ok", {"port": port, "n": st.get("in_use", 0),
                                           "total": st.get("tuners", 1)}, "\n".join(lines))
