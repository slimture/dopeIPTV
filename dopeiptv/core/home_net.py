"""The home network when it is split in parts (VLANs, subnets): the other
networks to search, and who counts as being at home.

A search that asks the whole network at once - mDNS (Bonjour), SSDP, an
HDHomeRun's or Jellyfin's broadcast, Plex's GDM - stays on the part of the
network it was sent on, unless the router passes it on. A question sent to
one address crosses to the other parts like any other connection. So Nerd
> Network keeps a list of the other networks (``nerd_other_networks``): a
network as 192.168.20.0/24, a stretch as 192.168.20.10-192.168.20.60 (or
192.168.20.10-60), or one address, one or more per line. "Find on my
network" and the cast window then ask each address there directly
(providers/lan_scan.py). Only home addresses (10/8, 172.16/12, 192.168/16
and the shared 100.64/10), a /22 at most per entry and 2048 addresses in
all, so a search stays a matter of seconds; a line that cannot be used says
why (``REASONS``). Only while Nerd is on.

``is_home_client`` is what the remote API and the phone remote let in: this
computer and every private range - not only the part this computer is on.
What needs no token - server mode's HDHomeRun tuner - lets in less
(``LocalNetworks``): this computer, the networks of its own addresses
(``own_networks``) and, while Nerd is on, the other networks listed here;
not every private range (a VPN's other customers, carrier NAT). Of its own
networks only those of the home count (``trusted``): never a tunnel's (a
VPN's subnet holds its other customers, ``is_tunnel``), nor a public, carrier
NAT or implausibly wide one. A network the person wants in all the same -
their Tailscale's, a VPN's - they list in Nerd's other networks.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .log import log
from .nerd_check import FAIL, INFO, OK, Check

KEY = "nerd_other_networks"
#: The widest network one line may name (a /22: 1024 addresses).
MAX_PREFIX = 22
MAX_ENTRY = 2 ** (32 - MAX_PREFIX)
#: Every line together.
MAX_TOTAL = 2048
#: Why an entry is refused (the check line's words, ``nerd_ck_ln_<why>``).
REASONS = ("form", "ipv6", "public", "big", "order", "total")

#: Where a home network's addresses are: RFC 1918, and the shared range
#: (RFC 6598) that carrier NAT and mesh VPNs (Tailscale) hand out.
HOME_V4 = tuple(ipaddress.IPv4Network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"))
#: Who may talk to the remote API and the phone remote: the above, this
#: computer, link-local, and IPv6's unique local and link-local addresses.
CLIENT_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = HOME_V4 + (
    ipaddress.IPv4Network("127.0.0.0/8"), ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv6Network("::1/128"), ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"))

_SPLIT = re.compile(r"[\s,;]+")


@dataclass(frozen=True)
class Entry:
    line: int           # 1-based, as the box numbers its lines
    text: str           # as typed
    first: int          # the addresses it covers, as integers
    last: int

    @property
    def size(self) -> int:
        return self.last - self.first + 1


@dataclass(frozen=True)
class Problem:
    line: int
    text: str
    reason: str         # one of REASONS


# -- one entry ---------------------------------------------------------------------

def _v4(text: str) -> ipaddress.IPv4Address:
    """An IPv4 address written out in full (no "10.1" shorthand)."""
    if text.count(".") != 3:
        raise ValueError(text)
    return ipaddress.IPv4Address(text)


def _home(first: int, last: int) -> bool:
    return any(int(net.network_address) <= first and last <= int(net.broadcast_address)
               for net in HOME_V4)


def read_entry(text: str) -> tuple[int, int] | str:
    """(first, last) of what *text* names, or why it cannot be searched
    (one of REASONS but "total")."""
    s = text.strip()
    if ":" in s:
        try:
            ipaddress.ip_network(s, strict=False)
        except ValueError:
            return "form"
        return "ipv6"
    try:
        if "/" in s:
            addr, _, bits = s.partition("/")
            if not bits.isdigit():
                return "form"
            net = ipaddress.IPv4Network(f"{_v4(addr)}/{int(bits)}", strict=False)
            first, last = int(net.network_address), int(net.broadcast_address)
        elif "-" in s:
            a, _, b = s.partition("-")
            start = _v4(a.strip())
            b = b.strip()
            if b.isdigit() and int(b) <= 255:
                end = ipaddress.IPv4Address((int(start) & ~0xFF) | int(b))
            else:
                end = _v4(b)
            first, last = int(start), int(end)
            if last < first:
                return "order"
        else:
            first = last = int(_v4(s))
    except ValueError:
        return "form"
    if not _home(first, last):
        return "public"
    if last - first + 1 > MAX_ENTRY:
        return "big"
    return first, last


# -- the list ------------------------------------------------------------------------

def tokens(value: Any) -> list[tuple[int, str]]:
    """(line number, text) of each entry: several on a line may be apart by
    spaces or commas; blank lines and # comments say nothing."""
    raw = [str(v) for v in value] if isinstance(value, (list, tuple)) \
        else str(value or "").splitlines()
    out: list[tuple[int, str]] = []
    for i, line in enumerate(raw, 1):
        s = line.split("#", 1)[0].strip()
        out += [(i, t) for t in _SPLIT.split(s) if t]
    return out


def read(value: Any) -> tuple[list[Entry], list[Problem]]:
    """The entries that can be searched, in order, and each that cannot and
    why. One that would take the whole past MAX_TOTAL is refused as "total"
    (an address named twice counts once)."""
    entries: list[Entry] = []
    problems: list[Problem] = []
    covered: list[tuple[int, int]] = []
    total = 0
    for n, text in tokens(value):
        got = read_entry(text)
        if isinstance(got, str):
            problems.append(Problem(n, text, got))
            continue
        first, last = got
        new = (last - first + 1) - _overlap(covered, first, last)
        if total + new > MAX_TOTAL:
            problems.append(Problem(n, text, "total"))
            continue
        total += new
        covered.append((first, last))
        entries.append(Entry(n, text, first, last))
    return entries, problems


def _overlap(covered: list[tuple[int, int]], first: int, last: int) -> int:
    """How many of first..last are already in *covered* (its stretches may
    overlap each other)."""
    spans = sorted((max(a, first), min(b, last)) for a, b in covered if a <= last and b >= first)
    seen, upto = 0, first - 1
    for a, b in spans:
        a = max(a, upto + 1)
        if a <= b:
            seen += b - a + 1
            upto = b
    return seen


def problems(value: Any) -> list[Problem]:
    return read(value)[1]


def addresses(value: Any, skip: Any = ()) -> list[str]:
    """Every address the list asks to search, each once, in order: of a
    network written as one (a /30 or wider), not its first and last - no
    device has those. *skip*: addresses searched anyway (this computer's
    own network)."""
    entries, _bad = read(value)
    seen = {str(a) for a in skip}
    out: list[str] = []
    for e in entries:
        first, last = e.first, e.last
        if "/" in e.text and e.size >= 4:
            first, last = first + 1, last - 1
        for i in range(first, last + 1):
            a = str(ipaddress.IPv4Address(i))
            if a not in seen:
                seen.add(a)
                out.append(a)
    return out


def configured(settings_or_nerd: Any = None) -> list[str]:
    """The addresses to search besides this computer's own network: only
    while Nerd is on. *settings_or_nerd*: a core.nerd.Nerd, or settings;
    the profile in use when None."""
    from . import nerd
    n = settings_or_nerd
    if n is None:
        n = nerd.current()
    elif not isinstance(n, nerd.Nerd):
        n = nerd.Nerd(n)
    if not n.on:
        return []
    return addresses(n.get(KEY))


def check_list(value: Any) -> Check:
    """The line under the box, without asking the network: the first entry
    that cannot be searched and why, else how much will be."""
    entries, bad = read(value)
    if bad:
        p = bad[0]
        detail = "\n".join(f"Line {b.line}: {b.text[:60]} - {b.reason}" for b in bad)
        return Check(FAIL, "nerd_ck_ln_" + p.reason,
                     {"n": p.line, "line": p.text[:60], "max": MAX_TOTAL,
                      "prefix": MAX_PREFIX}, detail)
    if not entries:
        return Check(INFO, "nerd_ck_ln_none")
    count = len(addresses(value))
    detail = "\n".join(e.text for e in entries)
    return Check(OK, "nerd_ck_ln_list", {"nets": len(entries), "n": count}, detail)


# -- who is at home ---------------------------------------------------------------------

def _ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = (address or "").strip().strip("[]").split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_home_client(address: str, own: str = "") -> bool:
    """Whether a client at *address* is on this computer or the home
    network: loopback, a private IPv4 range (any of them, not only the one
    this computer is on), link-local, IPv6 unique local or link-local. An
    IPv4 address written as IPv6 (::ffff:10.0.0.5) counts as itself. *own*:
    the address the client reached this computer at - a client at that
    same address is this computer, whatever its address is."""
    ip = _ip(address)
    if ip is None:
        return False
    if own and _ip(own) == ip:
        return True
    return any(ip.version == net.version and ip in net for net in CLIENT_NETS)


# -- the names a home network has ---------------------------------------------------------

#: Name endings a home network's own names have: RFC 6762's .local (mDNS),
#: RFC 8375's home.arpa, ICANN's .internal for private use, the routers'
#: usual .lan, .home and .localdomain, AVM's .fritz.box (a Fritz!Box answers
#: names under it itself) and Tailscale's MagicDNS .ts.net (answered inside
#: the tailnet). A deliberate list, kept short: a DNS rebinding needs a name
#: whose answers a web page's owner sets, and these are answered at home.
HOME_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain",
                 ".fritz.box", ".ts.net")
#: What a Host is written with when an address of the app's own is made
#: from it: a name, an IPv4 or a bracketed IPv6 address, a port.
_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:[]")


def host_name(host: str | None) -> str:
    """The name or address in a ``Host`` header, without its port and
    brackets, in lower case; "" for none (an unclosed bracket)."""
    name = (host or "").strip().lower()
    if name.startswith("["):                       # [IPv6]:port
        end = name.find("]")
        if end < 0:
            return ""
        name = name[1:end]
    elif name.count(":") == 1:
        name = name.rsplit(":", 1)[0]
    return name.rstrip(".")


def home_host(host: str | None) -> bool:
    """Whether the ``Host`` a request came under is a name of the home
    network: an IP address, localhost, this computer's own name, a name
    without a dot, or one with a home ending (HOME_SUFFIXES). A web page
    whose own name a DNS rebinding pointed at this computer asks under that
    public name - which is none of these. Devices at home ask by address,
    or by a name their router or mDNS answers."""
    name = host_name(host)
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if name == "localhost" or "." not in name or name.endswith(HOME_SUFFIXES):
        return True
    try:
        own = socket.gethostname().lower().rstrip(".")
    except OSError:
        own = ""
    return bool(own) and name == own


def host_for_addresses(host: str | None) -> str:
    """*host* as an address of the app's own may be written with it - a
    playlist's stream addresses, a tuner's lineup: the ``Host`` the client
    asked under when it is a home network's name or an address
    (``home_host``) and holds only what a host is written with; else "".
    Never any Host a request happens to carry: an address made from one
    sends whoever uses it - and the token in it - wherever that Host
    points."""
    text = (host or "").strip()
    if not text or len(text) > 255 or any(c not in _HOST_CHARS for c in text):
        return ""
    return text if home_host(text) else ""


# -- this computer's own networks (what needs no token) ----------------------------------

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
#: Seconds this computer's own networks are kept; an address outside them has
#: them read again, at most every LOCAL_RETRY (a network that changed: another
#: Wi-Fi, a cable plugged in).
LOCAL_TTL = 60.0
LOCAL_RETRY = 5.0
# Linux's interface ioctls (linux/sockios.h), IFF_UP and IFF_POINTOPOINT.
_SIOCGIFFLAGS, _SIOCGIFADDR, _SIOCGIFNETMASK, _IFF_UP = 0x8913, 0x8915, 0x891B, 0x1
_IFF_POINTOPOINT = 0x10
_IF_INET6 = "/proc/net/if_inet6"

# The rule for what needs no token: which of this computer's own networks
# stand for the home. A tunnel's network is not one - a VPN's subnet holds
# its other customers, a mesh VPN's every device of the account - nor a public
# network (a modem in bridge mode hands a computer one), carrier NAT's shared
# 100.64/10 (another subscriber's) or a netmask wider than a home's. A network
# the person does want in - their Tailscale's, a VPN's - they add themselves
# in Nerd's other networks (KEY, ``configured``), which this never narrows.
#: The ranges a trusted own network lies in: RFC 1918, link-local, IPv6's
#: unique local and link-local.
LAN_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"), ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"), ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv6Network("fc00::/7"), ipaddress.IPv6Network("fe80::/10"))
#: The widest own network trusted, per IP version: a home's is a /24, at
#: most a /16; an interface's IPv6 network a /64, a whole site's a /48.
WIDEST = {4: 16, 6: 48}
#: Interfaces that are tunnels by name, where the system does not say so
#: (IFF_POINTOPOINT, Qt's Ppp and Virtual do): OpenVPN's tun and tap,
#: WireGuard, PPP, macOS's utun and IPsec, Tailscale, ZeroTier (Qt on
#: Windows names a tunnel "tunnel_<n>").
TUNNEL_NAMES = ("tun", "tap", "wg", "ppp", "utun", "ipsec", "tailscale", "zt")


def is_tunnel(name: str, point_to_point: bool = False) -> bool:
    """Whether the interface *name* is a tunnel (a VPN, PPP), not a network
    in the home."""
    return point_to_point or (name or "").lower().startswith(TUNNEL_NAMES)


def trusted(net: Network) -> bool:
    """Whether an own network may stand for the home in what needs no token
    (the rule above)."""
    return (net.prefixlen >= WIDEST[net.version]
            and any(net.network_address in lan and net.broadcast_address in lan
                    for lan in LAN_NETS))


def _network(address: str, prefix: int | str) -> Network | None:
    text = (address or "").split("%", 1)[0]
    try:
        return ipaddress.ip_network(f"{text}/{prefix}", strict=False)
    except ValueError:
        return None


def _qt_networks(qt: Any) -> list[Network]:
    """Every address of every interface that is up but a tunnel's, with its
    prefix, as Qt reads them (QNetworkInterface: Linux, macOS and Windows
    alike)."""
    out: list[Network] = []
    iface_cls = qt.QNetworkInterface
    up = iface_cls.InterfaceFlag.IsUp
    p2p = iface_cls.InterfaceFlag.IsPointToPoint
    tunnels = (iface_cls.InterfaceType.Ppp, iface_cls.InterfaceType.Virtual)
    for iface in iface_cls.allInterfaces():
        flags = iface.flags()
        if not flags & up:
            continue
        if iface.type() in tunnels or is_tunnel(iface.name(), bool(flags & p2p)):
            continue
        for entry in iface.addressEntries():
            if entry.prefixLength() >= 0:
                net = _network(entry.ip().toString(), entry.prefixLength())
                if net is not None:
                    out.append(net)
    return out


def _linux_networks() -> list[Network]:
    """The same from Linux itself, without Qt: each interface's IPv4 address
    and netmask (ioctl), and /proc/net/if_inet6's IPv6 ones; a tunnel's
    (IFF_POINTOPOINT, or its name) left out."""
    import fcntl
    import struct
    out: list[Network] = []
    tunnels: set[str] = set()
    for _index, name in socket.if_nameindex():
        req = struct.pack("256s", name.encode("utf-8", "replace")[:15])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                flags = struct.unpack("H", fcntl.ioctl(s.fileno(), _SIOCGIFFLAGS, req)[16:18])[0]
                if is_tunnel(name, bool(flags & _IFF_POINTOPOINT)):
                    tunnels.add(name)
                    continue
                if not flags & _IFF_UP:
                    continue
                addr = socket.inet_ntoa(fcntl.ioctl(s.fileno(), _SIOCGIFADDR, req)[20:24])
                mask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), _SIOCGIFNETMASK, req)[20:24])
            except OSError:
                continue            # no IPv4 address on it
        net = _network(addr, mask)
        if net is not None:
            out.append(net)
    try:
        with open(_IF_INET6, encoding="ascii") as fh:
            for line in fh:
                f = line.split()
                if len(f) >= 6 and len(f[0]) == 32 and not is_tunnel(f[5]) \
                        and f[5] not in tunnels:
                    text = ":".join(f[0][i:i + 4] for i in range(0, 32, 4))
                    net = _network(text, int(f[2], 16))
                    if net is not None:
                        out.append(net)
    except (OSError, ValueError):
        pass                        # no IPv6 here
    return out


def _routed_networks() -> list[Network]:
    """The last resort: the address each route leaves from (nothing is
    sent), as its /24 or /64. It cannot tell a tunnel's address from the
    home's; ``trusted`` still keeps out any but a home range."""
    out: list[Network] = []
    for family, probe, prefix in ((socket.AF_INET, "10.255.255.255", 24),
                                  (socket.AF_INET, "172.31.255.255", 24),
                                  (socket.AF_INET, "192.168.255.255", 24),
                                  (socket.AF_INET, "192.0.2.1", 24),
                                  (socket.AF_INET6, "fd00::1", 64),
                                  (socket.AF_INET6, "2001:db8::1", 64)):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                net = _network(str(s.getsockname()[0]), prefix)
        except OSError:
            continue
        if net is not None and net not in out:
            out.append(net)
    return out


def own_networks() -> list[Network]:
    """The networks of this computer's own addresses, IPv4 and IPv6,
    link-local too; a tunnel's left out. Qt's QNetworkInterface where the
    window has loaded QtNetwork already - never imported here: the server
    without a window has no Qt but QtCore - else Linux's own lists, else
    the /24 or /64 of each address a route leaves from. Local calls only;
    never raises."""
    qt = sys.modules.get("PyQt6.QtNetwork")
    if qt is not None:
        try:
            return _qt_networks(qt)
        except Exception as e:
            log.debug("own networks: Qt cannot list them (%s)", e)
    if sys.platform.startswith("linux"):
        try:
            found = _linux_networks()
            if found:
                return found
        except Exception as e:
            log.debug("own networks: Linux cannot list them (%s)", e)
    return _routed_networks()


class LocalNetworks:
    """Who may use what needs no token (server mode's HDHomeRun tuner and
    its discovery): this computer, the networks of its own addresses that
    stand for the home (``trusted``) and, while Nerd is on, the other
    networks Nerd lists (``configured``, as the person typed them) - never
    every private range. Kept LOCAL_TTL seconds; an address outside them
    has them read again, at most every LOCAL_RETRY. Asked on the server's
    threads, never the window's."""

    def __init__(self, networks: Callable[[], Iterable[Network]] | None = None,
                 others: Callable[[], Iterable[str]] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._networks = networks or own_networks
        self._others = others or configured
        self._clock = clock
        self._lock = threading.Lock()
        self._nets: list[Network] = []
        self._extra: frozenset[str] = frozenset()
        self._at: float | None = None

    def _read(self, now: float) -> None:
        try:
            nets = [n for n in self._networks() if trusted(n)]
        except Exception as e:
            log.debug("own networks not read: %s", e)
            nets = []
        try:
            extra = frozenset(str(a) for a in self._others())
        except Exception as e:
            log.debug("Nerd's other networks not read: %s", e)
            extra = frozenset()
        with self._lock:
            self._nets, self._extra, self._at = nets, extra, now

    def _holds(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        with self._lock:
            nets, extra = self._nets, self._extra
        return str(ip) in extra or any(ip.version == n.version and ip in n for n in nets)

    def allows(self, address: str, own: str = "") -> bool:
        """Whether a client at *address* (*own*: the address it reached this
        computer at) may."""
        ip = _ip(address)
        if ip is None:
            return False
        if ip.is_loopback or (own and _ip(own) == ip):
            return True
        now = self._clock()
        at = self._at
        if at is None or now - at >= LOCAL_TTL:
            self._read(now)
        elif now - at >= LOCAL_RETRY and not self._holds(ip):
            self._read(now)
        return self._holds(ip)
