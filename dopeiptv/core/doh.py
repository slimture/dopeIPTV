"""Names looked up over HTTPS (DNS over HTTPS, RFC 8484) - Nerd's own DNS
for a source, so the provider's hostnames never reach the network's own
resolver.

A question goes as the RFC's wire format (GET ?dns=, base64url) unless the
address ends in /resolve, which is the JSON API (name=&type=, Google's
form); an answer is read in whichever of the two the server sends back. The
presets are addressed by IP, so asking them needs no lookup of its own;
a custom server's own name is the one name the system still resolves.

Answers are kept for as long as their TTL says (at least MIN_TTL, at most
MAX_TTL); "no such name" for NEG_TTL. Whether the system's resolver may
take over when this fails is the caller's choice (core/net_route.py) - by
default it may not, and the failure says so.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import struct
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .log import log

A, AAAA, CNAME = 1, 28, 5
NOERROR, SERVFAIL, NXDOMAIN = 0, 2, 3

#: The servers each choice asks, the second when the first does not answer.
PRESETS: dict[str, tuple[str, ...]] = {
    "cloudflare": ("https://1.1.1.1/dns-query", "https://1.0.0.1/dns-query"),
    "quad9": ("https://9.9.9.9/dns-query", "https://149.112.112.112/dns-query"),
    "google": ("https://8.8.8.8/dns-query", "https://8.8.4.4/dns-query"),
}
NAMES = {"cloudflare": "Cloudflare", "quad9": "Quad9", "google": "Google"}

MIN_TTL, MAX_TTL, NEG_TTL = 30, 3600, 30
TIMEOUT = (3.0, 4.0)        # connect, read - per server
WIRE = "application/dns-message"
JSON = "application/dns-json"


class DohError(OSError):
    """No address for *host* from DNS over HTTPS. *missing*: the servers
    answered, and the name has no address (rather than: they did not
    answer)."""

    def __init__(self, host: str, reason: str, missing: bool = False) -> None:
        super().__init__(f"DNS over HTTPS has no address for {host}: {reason}")
        self.host, self.reason, self.missing = host, reason, missing


def servers_for(choice: str, url: str = "") -> tuple[str, ...]:
    """The servers a choice asks: a preset's, the custom address, or none
    ("", "off", or a custom one that is no web address)."""
    if choice in PRESETS:
        return PRESETS[choice]
    if choice == "custom" and valid_url(url):
        return (url.strip(),)
    return ()


def label(choice: str, url: str = "") -> str:
    """What to call the server: the preset's name, or the custom host."""
    if choice in NAMES:
        return NAMES[choice]
    try:
        return urlsplit(url.strip()).hostname or url.strip()
    except ValueError:
        return url.strip()


def valid_url(url: str) -> bool:
    try:
        u = urlsplit((url or "").strip())
    except ValueError:
        return False
    return u.scheme in ("https", "http") and bool(u.hostname)


def is_json_api(url: str) -> bool:
    return urlsplit(url).path.rstrip("/").endswith("/resolve")


# -- the wire format ---------------------------------------------------------

def encode_name(host: str) -> bytes:
    name = host.strip().rstrip(".")
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise ValueError(f"not a host name: {host!r}") from e
    out = b""
    for part in name.split("."):
        if not part or len(part) > 63:
            raise ValueError(f"not a host name: {host!r}")
        out += bytes([len(part)]) + part.encode("ascii")
    if len(out) > 254:
        raise ValueError(f"host name too long: {host!r}")
    return out + b"\x00"


def build_query(host: str, qtype: int, qid: int = 0) -> bytes:
    """A question for *host*'s records of *qtype*, recursion wanted. ID 0,
    as RFC 8484 asks, so answers can be cached on the way."""
    return struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + encode_name(host) + struct.pack("!HH", qtype, 1)


def _skip_name(data: bytes, off: int) -> int:
    for _hop in range(128):
        if off >= len(data):
            raise ValueError("name runs past the end")
        n = data[off]
        if n == 0:
            return off + 1
        if n & 0xC0 == 0xC0:
            if off + 2 > len(data):
                raise ValueError("pointer runs past the end")
            return off + 2
        if n & 0xC0:
            raise ValueError("bad label")
        off += 1 + n
    raise ValueError("name too long")


def parse_response(data: bytes, qtype: int) -> tuple[list[str], int, int]:
    """(addresses of *qtype* in the answer, their smallest TTL, the response
    code) from a wire-format answer. The server follows CNAMEs: the records
    at their end are in the same answer."""
    if len(data) < 12:
        raise ValueError("answer too short")
    _qid, flags, qd, an, _ns, _ar = struct.unpack("!HHHHHH", data[:12])
    if not flags & 0x8000:
        raise ValueError("not an answer")
    rcode = flags & 0x000F
    off = 12
    for _ in range(qd):
        off = _skip_name(data, off) + 4
    addrs: list[str] = []
    ttl = MAX_TTL
    for _ in range(an):
        off = _skip_name(data, off)
        if off + 10 > len(data):
            raise ValueError("record runs past the end")
        rtype, _rclass, rttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        rdata = data[off:off + rdlen]
        if len(rdata) != rdlen:
            raise ValueError("record runs past the end")
        off += rdlen
        if rtype == qtype == A and rdlen == 4:
            addrs.append(socket.inet_ntop(socket.AF_INET, rdata))
            ttl = min(ttl, rttl)
        elif rtype == qtype == AAAA and rdlen == 16:
            addrs.append(socket.inet_ntop(socket.AF_INET6, rdata))
            ttl = min(ttl, rttl)
    return addrs, ttl, rcode


def parse_json(obj: Any, qtype: int) -> tuple[list[str], int, int]:
    """The same from a JSON answer ({"Status": 0, "Answer": [...]})."""
    if not isinstance(obj, dict):
        raise ValueError("not a DNS answer")
    rcode = int(obj.get("Status", SERVFAIL))
    addrs: list[str] = []
    ttl = MAX_TTL
    for rec in obj.get("Answer") or []:
        if not isinstance(rec, dict) or int(rec.get("type", 0)) != qtype:
            continue
        data = str(rec.get("data") or "").strip()
        try:
            ip = ipaddress.ip_address(data)
        except ValueError:
            continue
        if (ip.version == 4) == (qtype == A):
            addrs.append(str(ip))
            ttl = min(ttl, int(rec.get("TTL", MIN_TTL)))
    return addrs, ttl, rcode


# -- asking ------------------------------------------------------------------

_cache: dict[tuple[tuple[str, ...], str, int], tuple[float, list[str]]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _ask(server: str, host: str, qtype: int, timeout: Any) -> tuple[list[str], int, int, str]:
    """One server, one question: (addresses, ttl, rcode, the form it came in)."""
    from ._lazy_requests import requests
    if is_json_api(server):
        r = requests.get(server, params={"name": host, "type": "A" if qtype == A else "AAAA"},
                         headers={"Accept": JSON}, timeout=timeout)
    else:
        q = base64.urlsafe_b64encode(build_query(host, qtype)).rstrip(b"=").decode("ascii")
        r = requests.get(server, params={"dns": q}, headers={"Accept": WIRE}, timeout=timeout)
    try:
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        kind = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if "json" in kind:
            return (*parse_json(r.json(), qtype), "JSON")
        return (*parse_response(r.content, qtype), "RFC 8484")
    finally:
        r.close()


class Resolver:
    """Asks *servers* in turn (the next when one does not answer)."""

    def __init__(self, servers: tuple[str, ...], timeout: Any = TIMEOUT) -> None:
        self.servers = tuple(servers)
        self.timeout = timeout
        self.use_cache = True
        self.last: dict[str, Any] = {}     # what the last question came to (for a check)

    def query(self, host: str, qtype: int) -> list[str]:
        """*host*'s addresses of *qtype*; [] when it has none. DohError when
        no server answered."""
        name = host.strip().rstrip(".").lower()
        key = (self.servers, name, qtype)
        now = time.monotonic()
        with _lock:
            hit = _cache.get(key)
        if self.use_cache and hit is not None and hit[0] > now:
            self.last = {"cached": True, "addresses": list(hit[1])}
            return list(hit[1])
        errors: list[str] = []
        for server in self.servers:
            t0 = time.monotonic()
            try:
                addrs, ttl, rcode, form = _ask(server, name, qtype, self.timeout)
            except Exception as e:
                errors.append(f"{server}: {type(e).__name__}: {e}")
                continue
            if rcode not in (NOERROR, NXDOMAIN):
                errors.append(f"{server}: DNS error {rcode}")
                continue
            keep = NEG_TTL if not addrs else max(MIN_TTL, min(MAX_TTL, ttl))
            with _lock:
                _cache[key] = (time.monotonic() + keep, list(addrs))
            self.last = {"cached": False, "server": server, "form": form, "rcode": rcode,
                         "ttl": ttl if addrs else 0, "ms": int((time.monotonic() - t0) * 1000),
                         "addresses": list(addrs)}
            log.debug("doh: %s %s -> %s (ttl %s, %s)", name, "A" if qtype == A else "AAAA",
                      addrs or "none", ttl, server)
            return list(addrs)
        self.last = {"errors": errors}
        raise DohError(name, "; ".join(errors) or "no server to ask")

    def addresses(self, host: str, family: int) -> list[str]:
        return self.query(host, AAAA if family == socket.AF_INET6 else A)


_resolvers: dict[tuple[str, ...], Resolver] = {}


def resolver(servers: tuple[str, ...]) -> Resolver:
    """The one resolver for these servers (their answers kept together)."""
    with _lock:
        r = _resolvers.get(servers)
        if r is None:
            r = _resolvers[servers] = Resolver(servers)
        return r
