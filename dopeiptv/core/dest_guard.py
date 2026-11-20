"""Where the app's own requests may land - every request made through
core/_lazy_requests.py and core/net_adapter.py, whoever asks.

Two rules, for the two ways an outsider steers a request somewhere it
should not go:

* **The connection.** A name that is not this computer's or a home name
  never reaches this computer or a link-local host (a router's own
  address, a cloud's metadata service). Checked on the socket itself,
  after it connects and before a byte is sent (``peer_refusal``,
  ``check_peer``) - so a name whose DNS answer changes between a check and
  the connection (DNS rebinding) is caught at the connection. Through a
  proxy the proxy connects; there the name is not checked here.
* **The redirect.** A redirect goes on only over http(s), never from the
  internet into the home network, and never to this computer or a
  link-local host unless the address it came from was that very kind
  (``redirect_refusal``): by the name as written and by what it resolves
  to. Asked for every redirect requests follows (``get_redirect_target``
  in core/_lazy_requests.py) and those the app follows by hand.

Stream addresses the player and recorder open themselves (mpv, FFmpeg) are
judged as written by core/stream_address.py; their own DNS lookups and
redirects are theirs - docs/SECURITY.md says so.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from . import home_net, stream_address

THIS_COMPUTER = stream_address.THIS_COMPUTER
LINK_LOCAL = stream_address.LINK_LOCAL
HOME = "the home network"

#: A cloud machine's metadata service (its credentials among what it
#: answers): link-local or not, reached only by its address as written.
_METADATA = frozenset({*stream_address._METADATA_IPS,
                       *map(ipaddress.ip_address, ("169.254.169.254", "169.254.170.2"))})
#: The most addresses one name's answer is looked through for a redirect.
_ANSWERS_MAX = 16


def ip_kind(address: str) -> str:
    """THIS_COMPUTER, LINK_LOCAL, HOME or "" (the internet) for an IP
    address, however it is spelt - an IPv4 one inside an IPv6 one counts
    as itself."""
    ip = stream_address._ip(str(address or ""))
    if ip is None:
        return ""
    if ip.is_loopback or ip.is_unspecified or (ip.version == 4 and ip.packed[0] == 0):
        return THIS_COMPUTER
    if ip.is_link_local or ip in stream_address._METADATA_IPS:
        return LINK_LOCAL
    if ip.is_private or not ip.is_global:
        return HOME
    return ""


def named_kind(host: str) -> str:
    """What a host says it is as written: THIS_COMPUTER, LINK_LOCAL, HOME
    (a private address, a name without a dot or with a home ending) or ""
    (a name or address on the internet). Never looked up."""
    name = str(host or "").strip().strip("[]").lower().rstrip(".")
    if not name:
        return ""
    kind = stream_address._host_kind(name)
    if kind:
        return kind
    if stream_address._ip(name) is not None:
        return ip_kind(name)
    if home_net.home_host(name) or name in stream_address._HOME_NAMES:
        return HOME
    return ""


def peer_refusal(host: str, peer: str) -> str:
    """Why a connection to *host* that reached *peer* may not be used: it
    is this computer while the name is not this computer's, or link-local
    while the name is neither link-local nor a home name (a device at home
    without a router's address answers its .local name from 169.254/16).
    "" when it may."""
    kind = ip_kind(peer)
    said = named_kind(host)
    if kind == THIS_COMPUTER and said != THIS_COMPUTER:
        return THIS_COMPUTER
    if kind == LINK_LOCAL and said not in (LINK_LOCAL, HOME):
        return LINK_LOCAL
    if kind == LINK_LOCAL and stream_address._ip(peer) in _METADATA and said != LINK_LOCAL:
        return LINK_LOCAL           # a cloud's metadata service, never by a home name
    return ""


def check_peer(conn: Any, sock: socket.socket) -> socket.socket:
    """urllib3's new connection *sock* for *conn*: returned when it may be
    used (``peer_refusal``), else closed and refused with urllib3's
    NewConnectionError - a failed connection to every caller. One through
    a proxy is the proxy's, and passes."""
    if getattr(conn, "_tunnel_host", None) or getattr(conn, "proxy", None):
        return sock
    try:
        peer = sock.getpeername()[0]
    except (OSError, IndexError, TypeError):
        return sock
    why = peer_refusal(str(getattr(conn, "host", "") or ""), str(peer))
    if not why:
        return sock
    try:
        sock.close()
    except OSError:
        pass
    from urllib3.exceptions import NewConnectionError
    raise NewConnectionError(conn, f"its name led to {why} - not connected")


def _resolved(host: str, port: int) -> list[str]:
    """The addresses *host* resolves to now; [] when it does not."""
    try:
        answers = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError):
        return []
    return [str(a[4][0]) for a in answers[:_ANSWERS_MAX]]


def redirect_refusal(origin: str, target: str, resolve: bool = True) -> str:
    """Why a redirect from *origin* to *target* (both whole addresses) is
    not followed; "" when it may be. Only http(s); from the internet never
    into the home network (but to the same host again); to this computer or
    a link-local host only from an address that was that very kind. By the
    name as written and - with *resolve* - by what it resolves to now."""
    try:
        t = urlsplit(target)
        o = urlsplit(origin)
    except ValueError:
        return "an address that cannot be read"
    if t.scheme.lower() not in ("http", "https"):
        return f"{t.scheme or 'no'}://"
    t_host = t.hostname or ""
    if not t_host:
        return "an address without a host"
    o_said = named_kind(o.hostname or "")
    from_internet = o_said == ""
    kinds = {named_kind(t_host)}
    if resolve and named_kind(t_host) == "":
        try:
            port = t.port or (443 if t.scheme.lower() == "https" else 80)
        except ValueError:
            return "an address that cannot be read"
        kinds |= {ip_kind(a) for a in _resolved(t_host, port)}
    for kind in (THIS_COMPUTER, LINK_LOCAL):
        if kind in kinds and o_said != kind:
            return kind
    # The same host again (http to https, another path) is where the
    # origin already connected: a name of the internet that the home's own
    # DNS answers with a home address - a server at home by its public
    # name - stays reachable.
    if HOME in kinds and from_internet and t_host.lower() != (o.hostname or "").lower():
        return HOME
    return ""
