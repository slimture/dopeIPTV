"""A source's own way onto the network (Nerd): which DNS looks its names up
(core/doh.py, or the system's), which IP version it connects over, and the
certificate pinned for it (core/cert_pin.py) - read from its options and
the Nerd page into a Route, and carried out for each connection by
open_connection(). The app's own requests take it through
core/net_adapter.py, the player and recordings through the bridge
(core/net_bridge.py).

With Nerd off, or nothing set, a route is inactive and nothing changes:
connections are made exactly as before. A connection through a proxy is
the proxy's to make - its DNS, its IP version - so a route only changes the
ones that are not. Names only a home network knows (nerd.is_home_host) are
always the system's to look up: no public DNS knows "nas.local".
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from . import cert_pin, doh
from .log import log

#: The IP versions a source can ask for ("": as the Nerd page says).
FAMILIES = ("", "ipv4", "ipv6", "prefer6")
#: Own DNS choices ("": as the Nerd page says; "off": the system's).
DNS_CHOICES = ("", "off", "cloudflare", "quad9", "google", "custom")


@dataclass(frozen=True)
class Route:
    servers: tuple[str, ...] = ()       # DNS over HTTPS servers; (): the system's DNS
    dns_label: str = ""
    fallback: bool = False              # the system's DNS when own DNS fails
    family: str = ""                    # the source's own IP version
    default_family: str = "auto"        # the Nerd page's, for own DNS
    pins: tuple[tuple[str, int, str], ...] = ()

    @property
    def resolves(self) -> bool:
        """Names are looked up here, not by the system as always."""
        return bool(self.servers) or bool(self.family)

    @property
    def active(self) -> bool:
        return self.resolves or bool(self.pins)

    def pin(self, host: str, port: int | None) -> str | None:
        h = (host or "").strip("[]").lower()
        for ph, pp, fp in self.pins:
            if ph == h and pp == (port or 443):
                return fp
        return None

    def families(self) -> list[int]:
        """The address families to try, in order."""
        fam = self.family or {"ipv4": "ipv4", "ipv6": "ipv6"}.get(self.default_family, "")
        v6 = socket.has_ipv6
        if fam == "ipv4":
            return [socket.AF_INET]
        if fam == "ipv6":
            return [socket.AF_INET6] if v6 else []
        if fam == "prefer6":
            return [socket.AF_INET6, socket.AF_INET] if v6 else [socket.AF_INET]
        return [socket.AF_INET, socket.AF_INET6] if v6 else [socket.AF_INET]

    def own_dns_for(self, host: str) -> bool:
        h = (host or "").strip("[]")
        if not self.servers or is_ip(h):
            return False
        from .nerd import is_home_host
        return not is_home_host(h)

    def changes(self, host: str, port: int | None, scheme: str = "https") -> bool:
        """Does this route connect to *host* other than the system would?"""
        h = (host or "").strip("[]").lower()
        if scheme == "https" and self.pin(h, port):
            return True
        if not self.resolves or not h or is_ip(h) or h == "localhost":
            return False
        return bool(self.family) or self.own_dns_for(h)

    def bridges(self, scheme: str, host: str, port: int | None) -> bool:
        """Does the player go through the bridge (core/net_bridge.py) for a
        stream at this address? More often than changes(): a stream on a
        public IP still sends the player on to names (a panel's load
        balancer, a playlist's segments) that own DNS should look up. Never
        for this computer, nor - with own DNS alone - for the home network."""
        h = (host or "").strip("[]").lower()
        if scheme == "https" and self.pin(h, port):
            return True
        if not self.resolves or not h or h == "localhost" or (is_ip(h) and ipaddress.ip_address(
                h.split("%", 1)[0]).is_loopback):
            return False
        if self.family:
            return True
        from .nerd import is_home_host
        return not is_home_host(h)


INACTIVE = Route()


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address((host or "").strip("[]").split("%", 1)[0])
        return True
    except ValueError:
        return False


def dns_choice(source: dict | None, n: Any) -> tuple[str, str]:
    """(choice, custom address) for a source: its own, else the Nerd page's."""
    src = source or {}
    choice = str(src.get("dns") or "").strip().lower()
    if choice:
        return choice, str(src.get("dns_url") or "").strip()
    return n.get("nerd_dns").strip().lower(), n.get("nerd_dns_url").strip()


_memo: tuple[Any, Any, Route] | None = None


def route_for(source: dict | None, n: Any = None) -> Route:
    """The route of a source (its options, as nerd.source() has them) under
    the Nerd profile *n* (the one in use when None)."""
    global _memo
    from . import nerd
    n = nerd.current() if n is None else n
    memo = _memo
    if memo is not None and memo[0] is n and memo[1] is source:
        return memo[2]
    if not n.on:
        route = INACTIVE
    else:
        src = source or {}
        choice, url = dns_choice(src, n)
        servers = doh.servers_for(choice, url) if choice != "off" else ()
        if choice == "custom" and not servers:
            # An address that is none: every lookup fails and says why -
            # never quietly the system's DNS instead.
            servers = (url or "(no address)",)
        fam = str(src.get("ip_family") or "").strip().lower()
        pin = cert_pin.pin_of(src)
        route = Route(servers=servers, dns_label=doh.label(choice, url) if servers else "",
                      fallback=n.flag("nerd_dns_fallback"),
                      family=fam if fam in FAMILIES else "",
                      default_family=n.get("nerd_ip_family", "auto") or "auto",
                      pins=(pin,) if pin else ())
    _memo = (n, source, route)
    return route


def current() -> Route:
    """The route of the source playing now (nerd.source())."""
    from . import nerd
    return route_for(nerd.source())


# -- connecting -----------------------------------------------------------------

def _connect(fam: int, ip: str, port: int, timeout: Any, source_address: Any = None,
             socket_options: Any = None) -> socket.socket:
    sock = socket.socket(fam, socket.SOCK_STREAM)
    try:
        for opt in socket_options or ():
            sock.setsockopt(*opt)
        if timeout is None or isinstance(timeout, (int, float)):
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect((ip, port))
        return sock
    except BaseException:
        sock.close()
        raise


def _system(host: str, port: int, fam: int) -> list[str]:
    seen: list[str] = []
    for info in socket.getaddrinfo(host, port, fam, socket.SOCK_STREAM):
        ip = str(info[4][0])
        if ip not in seen:
            seen.append(ip)
    return seen


def lookup(route: Route, host: str, port: int, fam: int) -> list[str]:
    """*host*'s addresses of family *fam* as *route* looks them up. Own DNS
    failing raises DohError - unless the route lets the system take over."""
    if route.own_dns_for(host):
        try:
            ips = doh.resolver(route.servers).addresses(host, fam)
            if ips or not route.fallback:
                return ips
        except doh.DohError as e:
            if not route.fallback:
                raise doh.DohError(host, f"{route.dns_label or 'the server'} did not answer "
                                         f"({e.reason}); the system's DNS is not used") from e
            log.info("own DNS failed for %s, the system's takes over: %s", host, e.reason)
    return _system(host, port, fam)


def open_connection(route: Route | None, host: str, port: int, timeout: Any = None,
                    source_address: Any = None, socket_options: Any = None) -> socket.socket:
    """A TCP connection to *host*:*port* as *route* says: its names looked up
    by its DNS, over its IP version, each address in turn. Without a route
    (or one that changes nothing for this host): as the system does it."""
    h = (host or "").strip("[]")
    if route is None or not route.resolves or is_ip(h) or not h:
        sock = socket.create_connection((h, port), timeout if isinstance(timeout, (int, float)) else None,
                                        source_address)
        for opt in socket_options or ():
            sock.setsockopt(*opt)
        return sock
    last: OSError | None = None
    dns_error: doh.DohError | None = None
    tried = 0
    fams = route.families()
    if not fams:
        raise OSError(f"{host}: this computer has no IPv6")
    for fam in fams:
        try:
            ips = lookup(route, h, port, fam)
        except doh.DohError as e:
            dns_error = e
            continue
        except OSError as e:
            last = e
            continue
        for ip in ips:
            tried += 1
            try:
                return _connect(fam, ip, port, timeout, source_address, socket_options)
            except OSError as e:
                last = e
    if tried:
        raise last or OSError(f"{host}: no connection")
    if dns_error is not None:
        raise dns_error
    if last is not None:
        raise last
    version = {"ipv4": "IPv4", "ipv6": "IPv6"}.get(route.family, "")
    if route.own_dns_for(h):
        raise doh.DohError(h, f"{route.dns_label or 'the server'} knows no "
                              f"{version + ' ' if version else ''}address for it", missing=True)
    raise socket.gaierror(f"{host} has no {version + ' ' if version else ''}address")


def url_host(url: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) of a web address."""
    try:
        u = urlsplit((url or "").strip())
        port = u.port
    except ValueError:
        return None
    scheme = (u.scheme or "").lower()
    if scheme not in ("http", "https") or not u.hostname:
        return None
    return scheme, u.hostname.lower(), port or (443 if scheme == "https" else 80)


# -- the Nerd lines ---------------------------------------------------------------

def check_dns(servers: tuple[str, ...], name: str, host: str, proxied: bool = False) -> Any:
    """(worker thread) *host* asked of the own DNS servers, A and AAAA -
    never of the system's (that would be the lookup this is there to
    avoid)."""
    from .nerd import is_home_host
    from .nerd_check import FAIL, INFO, OK, WARN, Check
    if not servers:
        return Check(INFO, "nerd_ck_dns_off")
    lines = [f"Server: {s}" for s in servers] + [f"Name: {host}"]
    if is_home_host(host):
        lines.append("A name on your home network: the system looks it up.")
        return Check(WARN, "nerd_ck_dns_home", {"host": host}, "\n".join(lines))
    r = doh.Resolver(servers)
    r.use_cache = False
    t0 = time.monotonic()
    found: list[str] = []
    for qtype, tag in ((doh.A, "A"), (doh.AAAA, "AAAA")):
        try:
            ips = r.query(host, qtype)
        except doh.DohError as e:
            lines.append(f"{tag}: {e.reason}")
            return Check(FAIL, "nerd_ck_dns_down", {"name": name}, "\n".join(lines))
        last = r.last
        lines.append(f"{tag}: {', '.join(ips) or 'none'}" + (
            f" (TTL {last.get('ttl')} s, {last.get('form')}, {last.get('server')})" if ips else ""))
        found += ips
    ms = int((time.monotonic() - t0) * 1000)
    lines.append(f"Took {ms} ms")
    detail = "\n".join(lines)
    if not found:
        return Check(FAIL, "nerd_ck_dns_none", {"host": host, "name": name}, detail)
    if proxied:
        return Check(WARN, "nerd_ck_dns_proxy", {}, detail)
    if any(s.lower().startswith("http://") for s in servers):
        return Check(WARN, "nerd_ck_dns_plain", {"name": name}, detail)
    return Check(OK, "nerd_ck_dns_ok", {"host": host, "addr": found[0], "name": name, "ms": ms}, detail)


def check_family(route: Route, url: str, timeout: float = 5.0) -> Any:
    """(worker thread) The source's host reached over its own IP version
    (prefer IPv6: both, and which one it takes)."""
    from .nerd_check import FAIL, INFO, OK, Check
    where = url_host(url if "://" in url else "http://" + url)
    if where is None or not route.family:
        return Check(INFO, "nerd_ck_no_source")
    _scheme, host, port = where
    lines = [f"Host: {host} port {port}" + (f" (own DNS: {route.dns_label})" if route.own_dns_for(host) else "")]

    def one(fam: str) -> tuple[bool, str, bool]:
        """(connected, address, the host has an address of this kind)."""
        name = "IPv6" if fam == "ipv6" else "IPv4"
        t0 = time.monotonic()
        try:
            sock = open_connection(replace(route, family=fam), host, port, timeout)
        except OSError as e:
            lines.append(f"{name}: {type(e).__name__}: {e}")
            none = isinstance(e, socket.gaierror) or getattr(e, "missing", False)
            return False, "", not none
        addr = str(sock.getpeername()[0])
        sock.close()
        lines.append(f"{name}: {addr}: connected ({int((time.monotonic() - t0) * 1000)} ms)")
        return True, addr, True

    if route.family == "prefer6":
        v6, a6, _h6 = one("ipv6")
        v4, a4, _h4 = one("ipv4")
        if not (v4 or v6):
            return Check(FAIL, "nerd_ck_unreachable", {}, "\n".join(lines))
        return Check(OK, "nerd_ck_ip_prefer6", {"v6": "✓" if v6 else "✗", "v4": "✓" if v4 else "✗",
                                                "addr": a6 or a4}, "\n".join(lines))
    ok, addr, has = one(route.family)
    ip = "IPv6" if route.family == "ipv6" else "IPv4"
    if ok:
        return Check(OK, "nerd_ck_ip_ok", {"ip": ip, "addr": addr}, "\n".join(lines))
    return Check(FAIL, "nerd_ck_ip_unreachable" if has else "nerd_ck_ip_none", {"ip": ip}, "\n".join(lines))
