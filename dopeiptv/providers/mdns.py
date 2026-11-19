"""Multicast DNS service discovery (RFC 6762 / 6763) - just enough of it to
find the instances of one service type on the home network: their name,
address and port. Only the standard library.

The question goes out from an ordinary port, which makes it a "legacy
unicast" query: every responder answers straight back to that port (RFC
6762, 6.7), so nothing here joins the multicast group or competes with the
system's own responder for port 5353.
"""
from __future__ import annotations

import ipaddress
import random
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any

from ..core import home_net
from ..core.log import log

MDNS_ADDR = ("224.0.0.251", 5353)
T_A, T_PTR, T_TXT, T_SRV = 1, 12, 16, 33
CLASS_IN = 1
# What an answer can be. A packet bigger than this is not one.
MAX_PACKET = 9000
# The answers one question takes: whoever floods it fills no memory.
MAX_PACKETS = 512
# Pointers followed while reading one name, before it counts as a loop.
_MAX_JUMPS = 32
# The longest a name is (RFC 1035: 255 octets); one longer is no name.
_MAX_NAME = 255

Name = tuple[str, ...]


@dataclass
class Instance:
    """One instance of a service: "Kodi (Living room)" at 192.168.1.20:8080."""

    name: str
    host: str
    port: int
    target: str = ""
    txt: dict[str, str] = field(default_factory=dict)


def labels(name: str) -> Name:
    """"_x._tcp.local." as its labels."""
    return tuple(p for p in name.strip().rstrip(".").split(".") if p)


def _key(name: Name) -> Name:
    return tuple(p.lower() for p in name)


def encode_name(name: Name) -> bytes:
    out = b""
    for part in name:
        raw = part.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise ValueError("a label is 1-63 bytes")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def query(questions: list[tuple[Name, int]], ident: int = 0) -> bytes:
    """A query packet asking each (name, type) of *questions*."""
    head = struct.pack("!HHHHHH", ident & 0xFFFF, 0, len(questions), 0, 0, 0)
    body = b"".join(encode_name(n) + struct.pack("!HH", t, CLASS_IN)
                    for n, t in questions)
    return head + body


def _read_name(data: bytes, at: int) -> tuple[Name, int]:
    """The name at *at* and where the record goes on after it, following
    compression pointers (with a guard against a packet that loops, and
    against a name longer than a name may be)."""
    parts: list[str] = []
    end = -1
    jumps = 0
    size = 0
    while True:
        n = data[at]
        if n == 0:
            at += 1
            break
        if n & 0xC0 == 0xC0:
            if end < 0:
                end = at + 2
            jumps += 1
            if jumps > _MAX_JUMPS:
                raise ValueError("a name that loops")
            at = ((n & 0x3F) << 8) | data[at + 1]
            continue
        if n & 0xC0:
            raise ValueError("an unknown label type")
        chunk = data[at + 1:at + 1 + n]
        if len(chunk) != n:
            raise ValueError("a label past the end")
        size += 1 + n
        if size > _MAX_NAME:
            raise ValueError("a name longer than a name is")
        parts.append(chunk.decode("utf-8", "replace"))
        at += 1 + n
    return tuple(parts), (end if end >= 0 else at)


def _txt(rdata: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i < len(rdata):
        n = rdata[i]
        item = rdata[i + 1:i + 1 + n].decode("utf-8", "replace")
        i += 1 + n
        key, _, value = item.partition("=")
        if key:
            out[key.lower()] = value
    return out


def parse(data: bytes) -> list[tuple[Name, int, Any]]:
    """Every record of a response, as (name, type, value): a name for PTR,
    (port, target) for SRV, an IPv4 address for A, a dict for TXT. Other
    types are skipped. Raises ValueError on a packet that is not DNS."""
    if len(data) < 12 or len(data) > MAX_PACKET:
        raise ValueError("not a DNS packet")
    try:
        _id, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
        if not flags & 0x8000:
            return []                   # a question, not an answer
        at = 12
        for _ in range(qd):
            _name, at = _read_name(data, at)
            at += 4
        out: list[tuple[Name, int, Any]] = []
        for _ in range(an + ns + ar):
            name, at = _read_name(data, at)
            rtype, _cls, _ttl, size = struct.unpack("!HHIH", data[at:at + 10])
            at += 10
            rdata = data[at:at + size]
            if len(rdata) != size:
                raise ValueError("a record past the end")
            if rtype == T_PTR:
                out.append((name, rtype, _read_name(data, at)[0]))
            elif rtype == T_SRV and size >= 7:
                port = struct.unpack("!H", rdata[4:6])[0]
                out.append((name, rtype, (port, _read_name(data, at + 6)[0])))
            elif rtype == T_A and size == 4:
                out.append((name, rtype, socket.inet_ntoa(rdata)))
            elif rtype == T_TXT:
                out.append((name, rtype, _txt(rdata)))
            at += size
        return out
    except (IndexError, struct.error) as e:
        raise ValueError(f"a damaged packet ({e})") from e


def at_home(address: str) -> bool:
    """Whether *address* is one a device of the home can have: a private,
    link-local or unique-local one (core/home_net.py) - never this
    computer's own loopback, a multicast or an unspecified one."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        return False
    return home_net.is_home_client(address)


def instances(packets: list[tuple[bytes, str]], service: str) -> list[Instance]:
    """The instances of *service* the (packet, sender) pairs describe. An
    instance whose host has no address record takes the sender's: that is
    who answered for it."""
    want = _key(labels(service))
    names: dict[Name, Name] = {}            # key -> the instance's own name
    srv: dict[Name, tuple[int, Name, str]] = {}
    # A host's address as the one who answered for it says it: nobody
    # else's word for where another's instance is.
    addrs: dict[tuple[str, Name], str] = {}
    txts: dict[Name, dict[str, str]] = {}
    for data, sender in packets:
        try:
            records = parse(data)
        except ValueError as e:
            log.debug("mdns: an answer from %s skipped (%s)", sender, e)
            continue
        for name, rtype, value in records:
            if rtype == T_PTR and _key(name) == want and value:
                names.setdefault(_key(value), value)
            elif rtype == T_SRV:
                srv[_key(name)] = (value[0], value[1], sender)
            elif rtype == T_A:
                addrs.setdefault((sender, _key(name)), value)
            elif rtype == T_TXT:
                txts[_key(name)] = value
    out: list[Instance] = []
    for key, full in names.items():
        if key not in srv:
            continue
        port, target, sender = srv[key]
        host = addrs.get((sender, _key(target))) or sender
        if host != sender and not at_home(host):
            # An answer may speak for a device elsewhere on the home network
            # (a reflector does) - not for this computer, nor for an address
            # out on the internet: that is where the stream's address would
            # be sent, not where a device of the home is.
            log.info("mdns: %s says an instance is at %s - left", sender, host)
            continue
        prefix = full[:len(full) - len(want)]
        label = ".".join(prefix or full)
        out.append(Instance(name=label, host=host, port=port,
                            target=".".join(target), txt=txts.get(key, {})))
    return out


def _missing_srv(packets: list[tuple[bytes, str]], service: str) -> list[Name]:
    want = _key(labels(service))
    ptr: dict[Name, Name] = {}
    have: set[Name] = set()
    for data, _sender in packets:
        try:
            records = parse(data)
        except ValueError:
            continue
        for name, rtype, value in records:
            if rtype == T_PTR and _key(name) == want and value:
                ptr.setdefault(_key(value), value)
            elif rtype == T_SRV:
                have.add(_key(name))
    return [full for key, full in ptr.items() if key not in have]


def _collect(sock: socket.socket, until: float,
             packets: list[tuple[bytes, str]]) -> None:
    while True:
        left = until - time.monotonic()
        if left <= 0:
            return
        sock.settimeout(min(left, 0.3))
        try:
            data, sender = sock.recvfrom(MAX_PACKET)
        except socket.timeout:
            continue
        except OSError as e:
            log.debug("mdns: receiving stopped (%s)", e)
            return
        if len(packets) >= MAX_PACKETS:
            return                      # a home's devices fit many times over
        packets.append((data, sender[0]))


def browse(service: str, timeout: float = 2.5,
           addr: tuple[str, int] = MDNS_ADDR,
           iface: str | None = None) -> list[Instance]:
    """Ask the network for *service* ("_xbmc-jsonrpc-h._tcp.local") and
    return the instances that answer within *timeout* seconds. *iface*: the
    address of the interface to ask from (the LAN's, not a VPN's)."""
    ident = random.randrange(1, 0xFFFF)
    packets: list[tuple[bytes, str]] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        if iface:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                socket.inet_aton(iface))
            except OSError:
                pass
        sock.bind(("", 0))
        ask = query([(labels(service), T_PTR)], ident)
        for _ in range(2):              # UDP: a question can simply get lost
            sock.sendto(ask, addr)
        _collect(sock, time.monotonic() + timeout, packets)
        # A responder that left the SRV out of its answer is asked for it.
        missing = _missing_srv(packets, service)
        if missing:
            sock.sendto(query([(n, T_SRV) for n in missing[:8]], ident), addr)
            _collect(sock, time.monotonic() + min(1.0, timeout), packets)
    except OSError as e:
        log.info("mdns: the question could not be sent (%s)", e)
    finally:
        sock.close()
    found = instances(packets, service)
    log.info("mdns: %d instance(s) of %s", len(found), service)
    return found
