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
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

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
