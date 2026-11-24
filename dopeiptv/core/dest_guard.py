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
  proxy the proxy connects: there the request's name is looked up here
  first and judged by the same rule (``proxied_refusal``, from
  core/_lazy_requests.py's adapter) - best effort, since the proxy's own
  lookup, and where it then connects, are the proxy's. A public
  name a source on the internet named for the app to fetch itself - a
  picture of its lists, its playlist's guide (``note_outside``) - never
  reaches the home network either, unless the person gave that name
  themselves: a source's own server, its backup addresses (``note_own``),
  or Plex's own names (``.plex.direct``, a server at home by design).
* **The redirect.** A redirect goes on only over http(s), never from the
  internet into the home network, and never to this computer or a
  link-local host unless the address it came from was that very kind
  (``redirect_refusal``): by the name as written and by what it resolves
  to. Asked for every redirect requests follows (``get_redirect_target``
  in core/_lazy_requests.py) and those the app follows by hand.

Stream addresses the player and recorder open themselves (mpv, FFmpeg) are
judged as written by core/stream_address.py; their own DNS lookups and
redirects are theirs - docs/SECURITY.md says so.

While offline mode is on (core/offline.py) a third rule comes first: no
connection reaches the internet (``check_peer``).
"""
from __future__ import annotations

import functools
import ipaddress
import socket
import threading
import time
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

#: Public names a source on the internet named for the app to fetch itself
#: (its lists' pictures, its playlist's guide): such a name never leads to
#: the home network (``note_outside``). At most this many kept; once a
#: source has named more, every public name is taken for one of them
#: (``_FULL``) - a list of thousands of picture hosts could otherwise push
#: an early name out before it is fetched, and that name would then reach
#: the home network.
OUTSIDE_MAX = 8192
#: Names the person gave themselves - a source's own server and its backup
#: addresses (``note_own``): theirs to resolve as they like, a server at
#: home by its public name among them.
_OUTSIDE: dict[str, None] = {}
_OWN: set[str] = set()
#: Set once more than OUTSIDE_MAX names were noted, until the app closes:
#: from then on no public name but the person's own (``note_own``) and
#: Plex's leads home - it fails closed rather than forget a name.
_FULL = [False]
_NAMES_LOCK = threading.Lock()
#: Plex's names for a server's own addresses ("192-168-1-5.<id>.plex.direct"
#: answers 192.168.1.5): a server at home, by design.
_HOME_BY_DESIGN = (".plex.direct",)


def _name_of(address_or_host: str) -> str:
    """The host of an address (or a bare host), lower case, without its
    port, its login or a trailing dot; "" when there is none."""
    text = str(address_or_host or "").strip()
    if "://" in text:
        try:
            return (urlsplit(text).hostname or "").rstrip(".").lower()
        except ValueError:
            return ""
    return text.strip("[]").rstrip(".").lower()


@functools.lru_cache(maxsize=4096)
def _public_name(head: str) -> str:
    """The public name in an address's head ("https://host:port"), or ""
    for an address, a home name or none - read once per head."""
    name = _name_of(head)
    return name if name and named_kind(name) == "" else ""


def note_outside(address: str) -> None:
    """*address* (or its host) was named by a source on the internet for
    the app to fetch itself: a public name of it may not lead home. Asked
    for every picture of a list, so read once per scheme and host part."""
    text = str(address or "")
    start = text.find("://")
    if start >= 0:
        ends = [i for i in (text.find("/", start + 3), text.find("?", start + 3)) if i >= 0]
        text = text[:min(ends)] if ends else text
    name = _public_name(text)
    if not name:
        return
    with _NAMES_LOCK:
        if name in _OUTSIDE:
            return
        if len(_OUTSIDE) >= OUTSIDE_MAX:
            _FULL[0] = True
            return
        _OUTSIDE[name] = None


def note_own(address: str) -> None:
    """*address* (or its host) is one the person gave: a source's own
    server or backup address. Never refused for leading home - a server at
    home by its public name (split-horizon DNS) answers there. Whoever runs
    that name's DNS can point it home too, and the app's requests to it -
    its lists, and the pictures and guide it names on that name - then
    reach a device at home: a GET of their choosing, unanswered to them,
    never this computer or a link-local host (peer_refusal holds those for
    every name). Not narrowed: told apart from a server that is at home by
    its public name, it is not - by its port, its paths or when it changes
    (a laptop coming home flips the answer too, and the rewind buffer's
    connection is one of these requests). docs/SECURITY.md says so."""
    name = _name_of(address)
    if name:
        with _NAMES_LOCK:
            _OWN.add(name)


def _outside_name(host: str) -> bool:
    name = _name_of(host)
    if not name or name.endswith(_HOME_BY_DESIGN):
        return False
    with _NAMES_LOCK:
        return name not in _OWN and (_FULL[0] or name in _OUTSIDE)


def forget_names() -> None:
    """Every name noted (the tests)."""
    with _NAMES_LOCK:
        _OUTSIDE.clear()
        _OWN.clear()
        _FULL[0] = False
        _proxied.clear()


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
    without a router's address answers its .local name from 169.254/16),
    or the home network while the name is a public one a source on the
    internet named (``note_outside``) and the person did not give
    (``note_own``). "" when it may."""
    kind = ip_kind(peer)
    said = named_kind(host)
    if kind == THIS_COMPUTER and said != THIS_COMPUTER:
        return THIS_COMPUTER
    if kind == LINK_LOCAL and said not in (LINK_LOCAL, HOME):
        return LINK_LOCAL
    if kind == LINK_LOCAL and stream_address._ip(peer) in _METADATA and said != LINK_LOCAL:
        return LINK_LOCAL           # a cloud's metadata service, never by a home name
    if kind == HOME and said == "" and _outside_name(host):
        return HOME
    return ""


def check_peer(conn: Any, sock: socket.socket) -> socket.socket:
    """urllib3's new connection *sock* for *conn*: returned when it may be
    used (``peer_refusal``), else closed and refused with urllib3's
    NewConnectionError - a failed connection to every caller. One through
    a proxy is the proxy's, and passes - but while offline mode is on
    (core/offline.py) no connection, the proxy's either, reaches the
    internet: one whose peer cannot be read goes nowhere then."""
    from . import offline
    proxied = bool(getattr(conn, "_tunnel_host", None) or getattr(conn, "proxy", None))
    try:
        peer = sock.getpeername()[0]
    except (OSError, IndexError, TypeError):
        peer = None
    if offline.on():
        gone = offline.peer_refusal(str(peer)) if peer is not None else offline.INTERNET
        if gone:
            try:
                sock.close()
            except OSError:
                pass
            offline.note_refused(str(getattr(conn, "host", "") or ""), "a connection")
            from urllib3.exceptions import NewConnectionError
            raise NewConnectionError(conn, f"offline mode: {gone} - not connected")
    if proxied or peer is None:
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


#: A proxied request's target looked up here (``proxied_refusal``): kept
#: this long per host and port, at most _PROXIED_MAX - pictures through a
#: proxy are thousands of requests to a few hosts.
PROXIED_TTL = 30.0
_PROXIED_MAX = 1024
_proxied: dict[tuple[str, int], tuple[float, str]] = {}


def proxied_refusal(url: str) -> str:
    """Why a request for *url* that goes through a proxy may not be sent:
    its host looked up here first and judged as ``peer_refusal`` judges a
    connection - this computer for a name that is not this computer's,
    link-local or a cloud's metadata service, and, for a public name a
    source on the internet named (``note_outside``), the home network.
    Any of its answers counts. "" when it may - also when the name does not
    resolve here: best effort, since the proxy looks the name up itself
    and connects where its own answer says (that lookup is the proxy's)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return "an address that cannot be read"
    if not host:
        return ""
    if stream_address._ip(host) is not None:
        return ""                           # an address as written: as it is
    now = time.monotonic()
    with _NAMES_LOCK:
        hit = _proxied.get((host, port))
    if hit is not None and now - hit[0] < PROXIED_TTL:
        return hit[1]
    why = next((w for w in (peer_refusal(host, a) for a in _resolved(host, port)) if w), "")
    with _NAMES_LOCK:
        if len(_proxied) >= _PROXIED_MAX:
            _proxied.clear()
        _proxied[(host, port)] = (now, why)
    return why


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
