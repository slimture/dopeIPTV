"""Trusting one certificate for one source (Nerd): a home server on https
with a certificate of its own making, which the system does not trust.

The user looks at it - subject, issuer, expiry, SHA-256 fingerprint - and
pins exactly that fingerprint for that source (its host and port). From
then on the app's own connections to it check the fingerprint instead of
the chain (core/net_adapter.py; the player's and recordings' through the
bridge, core/net_bridge.py); verification is never switched off for
anything else. A different certificate fails with PinMismatch, noted here
(mismatch()) so the window can offer to review the new one.

The certificate is read with a small DER reader of its own: the standard
library parses a certificate only once it has been verified.
"""

from __future__ import annotations

import hashlib
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

KEY, KEY_FOR = "cert_pin", "cert_pin_for"

# Attribute names of a certificate's subject and issuer, by OID.
_ATTRS = {"2.5.4.3": "CN", "2.5.4.10": "O", "2.5.4.11": "OU", "2.5.4.6": "C",
          "2.5.4.7": "L", "2.5.4.8": "ST", "1.2.840.113549.1.9.1": "E"}
_SAN = "2.5.29.17"


class PinMismatch(ssl.SSLError):
    """The server shows another certificate than the one pinned for it."""

    def __init__(self, host: str, port: int, pinned: str, got: str) -> None:
        super().__init__(f"The certificate of {host}:{port} changed: trusted SHA-256 {pretty(pinned)}, "
                         f"the server now shows {pretty(got)}")
        self.host, self.port, self.pinned, self.got = host, port, pinned, got


@dataclass
class CertInfo:
    der: bytes
    sha256: str
    subject: dict[str, str] = field(default_factory=dict)
    issuer: dict[str, str] = field(default_factory=dict)
    not_before: datetime | None = None
    not_after: datetime | None = None
    names: list[str] = field(default_factory=list)

    @property
    def self_signed(self) -> bool:
        return bool(self.subject) and self.subject == self.issuer

    def expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.not_after is not None and self.not_after < now

    def name(self, which: str = "subject") -> str:
        d = self.subject if which == "subject" else self.issuer
        return ", ".join(f"{k}={v}" for k, v in d.items()) or "-"


# -- fingerprints -------------------------------------------------------------

def fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def normal(text: str) -> str:
    """A fingerprint as stored (64 lower-case hex digits) from how it was
    written (colons, spaces, upper case); "" when it is none."""
    h = re.sub(r"[\s:\-]", "", text or "").lower()
    return h if re.fullmatch(r"[0-9a-f]{64}", h) else ""


def pretty(fp: str) -> str:
    """AB:CD:… - how a fingerprint is shown."""
    return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)).upper()


# -- reading a certificate ------------------------------------------------------

def _tlv(data: bytes, i: int) -> tuple[int, int, int]:
    """(tag, start of the value, end of the value) of the element at *i*."""
    if i + 2 > len(data):
        raise ValueError("truncated")
    tag, n = data[i], data[i + 1]
    i += 2
    if n & 0x80:
        count = n & 0x7F
        if not 0 < count <= 4 or i + count > len(data):
            raise ValueError("bad length")
        n = int.from_bytes(data[i:i + count], "big")
        i += count
    if i + n > len(data):
        raise ValueError("truncated")
    return tag, i, i + n


def _children(data: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    out, i = [], start
    while i < end:
        tag, s, e = _tlv(data, i)
        out.append((tag, s, e))
        i = e
    return out


def _oid(raw: bytes) -> str:
    if not raw:
        return ""
    parts = [raw[0] // 40, raw[0] % 40]
    v = 0
    for b in raw[1:]:
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(v)
            v = 0
    return ".".join(str(p) for p in parts)


def _text(tag: int, raw: bytes) -> str:
    if tag == 0x1E:
        return raw.decode("utf-16-be", "replace")
    if tag == 0x1C:
        return raw.decode("utf-32-be", "replace")
    if tag == 0x14:
        return raw.decode("latin-1")
    return raw.decode("utf-8", "replace")


def _name(data: bytes, s: int, e: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for _t, rs, re_ in _children(data, s, e):              # SET OF
        for _t2, as_, ae in _children(data, rs, re_):     # SEQUENCE { type, value }
            parts = _children(data, as_, ae)
            if len(parts) < 2 or parts[0][0] != 0x06:
                continue
            oid = _oid(data[parts[0][1]:parts[0][2]])
            key = _ATTRS.get(oid, oid)
            vt, vs, ve = parts[1]
            value = _text(vt, data[vs:ve])
            out[key] = f"{out[key]}, {value}" if key in out else value
    return out


def _time(tag: int, raw: bytes) -> datetime | None:
    text = raw.decode("ascii", "replace").rstrip("Z")
    try:
        if tag == 0x17:     # UTCTime: YYMMDDHHMM[SS]
            year = int(text[:2])
            year += 1900 if year >= 50 else 2000
            rest = text[2:]
        else:               # GeneralizedTime
            year, rest = int(text[:4]), text[4:]
        rest = rest.split(".")[0]
        sec = int(rest[8:10]) if len(rest) >= 10 else 0
        return datetime(year, int(rest[0:2]), int(rest[2:4]), int(rest[4:6]), int(rest[6:8]), sec,
                        tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def _alt_names(data: bytes, s: int, e: int) -> list[str]:
    """dNSName and iPAddress entries of a subjectAltName's value."""
    out: list[str] = []
    _t, gs, ge = _tlv(data, s)
    for tag, vs, ve in _children(data, gs, ge):
        raw = data[vs:ve]
        if tag == 0x82:
            out.append(raw.decode("ascii", "replace"))
        elif tag == 0x87 and len(raw) in (4, 16):
            out.append(socket.inet_ntop(socket.AF_INET if len(raw) == 4 else socket.AF_INET6, raw))
    return out


def parse(der: bytes) -> CertInfo:
    """What a person needs to see of a certificate. ValueError when *der*
    is no certificate this can read (the fingerprint still stands)."""
    info = CertInfo(der=der, sha256=fingerprint(der))
    _t, cs, ce = _tlv(der, 0)
    tbs_tag, ts, te = _tlv(der, cs)
    if _t != 0x30 or tbs_tag != 0x30:
        raise ValueError("not a certificate")
    items = _children(der, ts, te)
    if items and items[0][0] == 0xA0:       # [0] version
        items = items[1:]
    if len(items) < 6:
        raise ValueError("not a certificate")
    _serial, _alg, issuer, validity, subject = items[0], items[1], items[2], items[3], items[4]
    info.issuer = _name(der, issuer[1], issuer[2])
    info.subject = _name(der, subject[1], subject[2])
    times = _children(der, validity[1], validity[2])
    if len(times) == 2:
        info.not_before = _time(times[0][0], der[times[0][1]:times[0][2]])
        info.not_after = _time(times[1][0], der[times[1][1]:times[1][2]])
    for tag, s, _e in items[6:]:
        if tag != 0xA3:                     # [3] extensions
            continue
        _st, xs, xe = _tlv(der, s)
        for _xt, es, ee in _children(der, xs, xe):
            parts = _children(der, es, ee)
            if parts and _oid(der[parts[0][1]:parts[0][2]]) == _SAN:
                octets = parts[-1]
                try:
                    info.names = _alt_names(der, octets[1], octets[2])
                except ValueError:
                    pass
    return info


def read(der: bytes) -> CertInfo:
    """parse(), or what can be said of a certificate it cannot read."""
    try:
        return parse(der)
    except (ValueError, IndexError):
        return CertInfo(der=der, sha256=fingerprint(der))


# -- asking the server ------------------------------------------------------------

def host_port(url: str) -> tuple[str, int] | None:
    """(host, port) of an https address; None for anything else."""
    u = (url or "").strip()
    if "://" not in u:
        return None
    try:
        parts = urlsplit(u)
        port = parts.port or 443
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    return parts.hostname.lower(), port


def fetch(host: str, port: int, timeout: float = 8.0, route: Any = None) -> CertInfo:
    """The certificate *host*:*port* shows, read without trusting it (the
    connection made as *route* says: its own DNS, IP version)."""
    from . import net_route
    sock = net_route.open_connection(route, host, port, timeout)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with ctx.wrap_socket(sock, server_hostname=None if _is_ip(host) else host) as tls:
            der = tls.getpeercert(binary_form=True)
    finally:
        sock.close()
    if not der:
        raise ssl.SSLError("the server showed no certificate")
    return read(der)


def system_trust(host: str, port: int, timeout: float = 8.0, route: Any = None) -> tuple[bool, str]:
    """Does the system (the CA list requests uses) trust what *host* shows,
    for that name? (yes, "") or (no, why)."""
    from . import net_route
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    sock = net_route.open_connection(route, host, port, timeout)
    try:
        with ctx.wrap_socket(sock, server_hostname=host):
            return True, ""
    except ssl.SSLCertVerificationError as e:
        return False, str(e.verify_message or e)
    finally:
        sock.close()


def _is_ip(host: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


# -- a source's pin -------------------------------------------------------------

def pin_of(options: dict | None) -> tuple[str, int, str] | None:
    """(host, port, fingerprint) pinned in a source's options."""
    o = options or {}
    fp = normal(str(o.get(KEY) or ""))
    where = str(o.get(KEY_FOR) or "").strip().lower()
    host, _sep, port = where.rpartition(":")
    if not fp or not host or not port.isdigit():
        return None
    return host.strip("[]"), int(port), fp


def pin_options(host: str, port: int, fp: str) -> dict[str, str]:
    h = f"[{host}]" if ":" in host else host
    return {KEY: normal(fp), KEY_FOR: f"{h.lower()}:{port}"}


# What the app's connections met that was not what is pinned, by host:port:
# the window offers to look at it.
_mismatch: dict[tuple[str, int], tuple[str, float]] = {}
_lock = threading.Lock()


def record_mismatch(host: str, port: int, got: str) -> None:
    with _lock:
        _mismatch[(host.lower().strip("[]"), port)] = (got, time.time())


def mismatch(host: str, port: int) -> str | None:
    """The fingerprint *host*:*port* showed instead of the pinned one, when
    one of the app's connections met that."""
    with _lock:
        hit = _mismatch.get((host.lower().strip("[]"), port))
    return hit[0] if hit else None


def clear_mismatch(host: str, port: int) -> None:
    with _lock:
        _mismatch.pop((host.lower().strip("[]"), port), None)


def mismatch_in(exc: BaseException | None) -> PinMismatch | None:
    """The PinMismatch somewhere in what *exc* wraps (requests and urllib3
    wrap it twice over)."""
    seen: set[int] = set()
    stack: list[Any] = [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, PinMismatch):
            return e
        stack += [getattr(e, "reason", None), e.__cause__, e.__context__]
        stack += [a for a in getattr(e, "args", ()) if isinstance(a, BaseException)]
    return None


# -- the Nerd line ---------------------------------------------------------------

def check(url: str, pinned: tuple[str, int, str] | None, route: Any = None,
          timeout: float = 8.0) -> Any:
    """(worker thread) What the server of *url* shows now, against what is
    pinned for it - or, with no pin, whether the system trusts it."""
    from .nerd_check import FAIL, INFO, OK, WARN, Check
    hp = host_port(url)
    if hp is None:
        return Check(INFO, "nerd_ck_pin_plain")
    host, port = hp
    lines = [f"TLS {host}:{port}"]
    t0 = time.monotonic()
    try:
        info = fetch(host, port, timeout, route)
    except (OSError, ValueError) as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_unreachable", {}, "\n".join(lines))
    lines += describe(info) + [f"Read in {int((time.monotonic() - t0) * 1000)} ms"]
    date = info.not_after.strftime("%Y-%m-%d") if info.not_after else "?"
    pin = pinned if pinned and (pinned[0], pinned[1]) == (host, port) else None
    if pin is not None:
        lines.insert(1, f"Trusted: SHA-256 {pretty(pin[2])}")
        if info.sha256 != pin[2]:
            return Check(FAIL, "nerd_ck_pin_changed", {}, "\n".join(lines))
        if info.expired():
            return Check(WARN, "nerd_ck_pin_expired", {"date": date}, "\n".join(lines))
        clear_mismatch(host, port)
        return Check(OK, "nerd_ck_pin_ok", {"date": date}, "\n".join(lines))
    try:
        trusted, why = system_trust(host, port, timeout, route)
    except OSError as e:
        trusted, why = False, f"{type(e).__name__}: {e}"
    lines.append(f"The system trusts it: {'yes' if trusted else 'no - ' + why}")
    if trusted:
        return Check(OK, "nerd_ck_pin_system", {"date": date}, "\n".join(lines))
    return Check(WARN, "nerd_ck_pin_untrusted", {}, "\n".join(lines))


def describe(info: CertInfo) -> list[str]:
    """The certificate in lines, as the details show it."""
    when = "%Y-%m-%d %H:%M UTC"
    return [f"Subject: {info.name('subject')}",
            f"Issuer: {info.name('issuer')}" + (" (self-signed)" if info.self_signed else ""),
            f"Valid from: {info.not_before.strftime(when) if info.not_before else '?'}",
            f"Valid until: {info.not_after.strftime(when) if info.not_after else '?'}"
            + (" - expired" if info.expired() else ""),
            f"Names: {', '.join(info.names) or '-'}",
            f"SHA-256: {pretty(info.sha256)}"]
