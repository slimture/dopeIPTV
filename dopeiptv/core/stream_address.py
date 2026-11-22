"""Which of a source's addresses are played.

A channel's address is the provider's word - a line of its playlist, a
portal's answer - and it goes to mpv, to the recorder and to an external
player as it is. Each takes far more than streams there:

- ``av://lavfi:`` builds an FFmpeg filter graph, and a graph can write a
  file wherever it is told to (``metadata=mode=print:file=...``, with words
  of the sender's choice in it);
- ``edl://``, ``memory://`` and ``hex://`` carry a whole edit list or file
  in the address; ``lavf://`` and ``ffmpeg://`` reach every FFmpeg protocol;
- ``gopher://`` sends bytes of the sender's choice to any host and port,
  this computer's own too; ``fdclose://`` closes one of the app's own files;
- a line that begins with ``-`` is an option to a player started with it
  (``--script=...`` runs a script).

mpv refuses these from a playlist it reads itself - but the app reads the
playlist and hands mpv one address at a time, which mpv takes as the
person's own. So an address from a source is looked at first
(:func:`refusal`):

- from a source on the internet (its own address - the playlist's, the
  portal's, the server's - is a public name or address, :func:`on_internet`):
  a stream over a media protocol (:data:`MEDIA`) and nothing else - no file
  share, file transfer, raw socket or WebDAV (on Windows an ``smb://``
  address signs in to that host with the person's own account, a login hash
  handed over; ``tcp://`` and ``tls://`` talk to any service), and never an
  address on this computer or a link-local one (:func:`host_refusal`: a
  provider reaching the printer's page, a local engine or a cloud's
  metadata service through the player). A private address of the home
  network stays a stream's address: an ISP's multicast proxy, udpxy on the
  router;
- from a source on the home network (a box, a NAS's playlist, a media
  server, a playlist server on this computer): a stream over one of the
  network's protocols (:data:`NETWORK`) and nothing else - no file, no disc
  or device of this computer, and no ``ytdl://`` (yt-dlp run on a page of
  the sender's choice);
- from a playlist file on this computer: anything but what is no stream at
  all (:data:`NEVER`, the ``lavfi`` device, an option, control characters) -
  files anywhere, a disc, a tuner card and a capture device stay the
  person's own to list.

An address the person's own rewrite rules made, or their udpxy, is theirs
(providers/client.py ``_nerd_lists``): the source's word is judged first,
what the person made of it after.

A source's pictures (logos, posters, stills) are fetched by the app itself,
so a source on the internet that lists one on this computer or a link-local
host would have the app send requests there: the same host rule takes them
out of its rows (:func:`picture_refusal`, providers/client.py
``_pictures_kept``) - a home network's or a playlist file's pictures, and an
internet source's on a private address of the home network, as before.

A refused address is no address: the channel stays in its list and does
not play, as one the source gave no address for. Nothing here asks the
network or the disk: a host is read as written, never looked up - a name
whose DNS answer is this computer, or a server that redirects there, is
not seen here (docs/SECURITY.md).
"""

from __future__ import annotations

import functools
import ipaddress
import re
import socket
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit

from .log import log

#: The network's media protocols as mpv names them: what a provider's
#: stream comes over. Not ``ytdl://``: that runs yt-dlp on whatever page
#: follows it - the app's own word for a web stream the person opened
#: (core/ytdlp.mpv_address, which never passes a source), never a source's.
NETWORK = frozenset((
    "http", "https", "httpproxy", "rtsp", "rtsps", "rtmp", "rtmpe", "rtmps",
    "rtmpt", "rtmpte", "rtmpts", "rtp", "srtp", "udp", "mms", "mmsh",
    "mmshttp", "mmst", "srt", "rist", "ftp", "sftp", "smb", "tcp", "tls",
    "dav", "davs", "webdav", "webdavs", "ipfs", "ipns"))

#: What a source on the internet may hand out: the media protocols, each a
#: stream from a server or a multicast group - none of the network's file
#: shares, file transfers, raw sockets, WebDAV, proxies or gateways.
MEDIA = frozenset((
    "http", "https", "rtsp", "rtsps", "rtmp", "rtmpe", "rtmps", "rtmpt", "rtmpte",
    "rtmpts", "rtp", "srtp", "udp", "mms", "mmsh", "mmshttp", "mmst", "srt", "rist"))

#: No stream, whoever lists it: an address that carries a file or an edit
#: list in itself, reaches every FFmpeg protocol or a socket of this
#: computer, sends the sender's bytes to a port, or touches the app's own
#: open files.
NEVER = frozenset((
    "edl", "memory", "hex", "lavf", "ffmpeg", "gopher", "gophers", "unix",
    "fd", "fdclose", "slice", "md5", "null", "data"))

#: FFmpeg's devices behind av:// and avdevice://. One of them is no device:
#: lavfi is a filter graph, which reads and writes files.
_DEVICE_SCHEMES = frozenset(("av", "avdevice"))
_GRAPH_DEVICES = frozenset(("lavfi",))

#: This computer by name: what the system's resolver answers itself
#: (RFC 6761's .localhost, the names Linux distributions give ::1).
_THIS_COMPUTER = frozenset((
    "localhost", "localhost.localdomain", "localhost4", "localhost4.localdomain4",
    "localhost6", "localhost6.localdomain6", "ip6-localhost", "ip6-loopback"))
#: A cloud machine's metadata service (its credentials among what it
#: answers), by its names and its addresses outside link-local.
_METADATA_NAMES = frozenset((
    "metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal"))
_METADATA_IPS = frozenset((ipaddress.ip_address("100.100.100.200"),
                           ipaddress.ip_address("fd00:ec2::254")))
#: Where the host's part of an address ends, as each reader has it: Python
#: and FFmpeg 5 on, FFmpeg 4 (a "#" no end), and the widest (a browser's
#: backslash, the "|" before Kodi-style headers, rtmp's options).
_HOST_ENDS = ("/?#", "/?", "/?#\\| \t")
#: Why a host is no stream's from the internet - the log's words for it.
THIS_COMPUTER = "this computer"
LINK_LOCAL = "a link-local host"
#: A host of the home network - refused to a source on the internet unless
#: the person let that source in (its option HOME_OPTION).
HOME = "the home network"
#: A source's own option (in its options): its addresses may lead into the
#: home network - a list of the person's own on the internet naming boxes
#: at home. Off: what a playlist on the internet names never sends the app
#: to a device at home.
HOME_OPTION = "home_addresses"
#: The end of a refusal only a source on the internet gets.
_INTERNET = " from the internet"
#: AceStream's engine on this computer: its HTTP API on port 6878 plays the
#: content id it is given, and an AceStream playlist on the internet names
#: its streams there - what the person's list is for. The one place here a
#: source on the internet may send the player: plain http to that port of
#: the loopback address or localhost, exactly as written, a path under /ace/.
_ACESTREAM_NETLOCS = frozenset(("127.0.0.1:6878", "localhost:6878", "[::1]:6878"))


def scheme_of(address: str) -> str:
    """The protocol of *address* as mpv reads it, lower case: a letter, then
    letters, digits, ``+``, ``-`` or ``.``, before ``://``. "" for a path."""
    head, sep, _rest = address.partition("://")
    if not sep or not head or not head[0].isascii() or not head[0].isalpha():
        return ""
    if not all(c.isascii() and (c.isalnum() or c in "+-.") for c in head):
        return ""
    return head.lower()


def _device(address: str) -> str:
    """The FFmpeg device an av:// address names ("lavfi" in
    av://lavfi:testsrc), lower case."""
    return address.partition("://")[2].partition(":")[0].strip().lower()


# -- the host an address names ------------------------------------------------------

def _inet_aton(text: str) -> ipaddress.IPv4Address | None:
    """An IPv4 address as the C library reads one (inet_aton, which the
    system's resolver takes before it asks DNS): one to four parts, each
    decimal, octal (a leading 0) or hex (0x), the last filling the bytes
    left - 127.1, 0x7f000001, 2130706433 and 0177.0.0.1 are all 127.0.0.1."""
    parts = text.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values: list[int] = []
    for part in parts:
        low = part.lower()
        if low.startswith("0x"):
            digits, base = low[2:], 16
            if not all(c in "0123456789abcdef" for c in digits):
                return None
        elif len(low) > 1 and low.startswith("0"):
            digits, base = low[1:], 8
            if not all(c in "01234567" for c in digits):
                return None
        else:
            digits, base = low, 10
            if not digits or not all(c in "0123456789" for c in digits):
                return None
        values.append(int(digits, base) if digits else 0)
    last = values.pop()
    if any(v > 0xFF for v in values) or last >= 1 << (8 * (4 - len(values))):
        return None
    number = 0
    for v in values:
        number = number << 8 | v
    return ipaddress.IPv4Address(number << (8 * (4 - len(values))) | last)


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """*host* as an IP address however it is spelt - an IPv4 one written
    inside an IPv6 one as itself; None for a name."""
    text = host.strip("[]").split("%", 1)[0]
    if not text:
        return None
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        ip = _inet_aton(text)
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour
        if inner is None and int(ip) >> 32 == 0 and int(ip) > 1:
            inner = ipaddress.IPv4Address(int(ip))      # ::a.b.c.d, IPv4-compatible
        if inner is not None:
            return inner
    return ip


def _own_names() -> set[str]:
    """This computer's own name, as its resolver answers it with its own
    addresses (no lookup: the system's host name)."""
    try:
        name = socket.gethostname().strip().lower().rstrip(".")
    except OSError:
        return set()
    return {name, name.split(".", 1)[0]} - {""}


def _host_kind(host: str) -> str:
    """THIS_COMPUTER, LINK_LOCAL or "" for one host as an address names it."""
    name = host.strip().lower().rstrip(".")
    if not name:
        return ""
    if name in _THIS_COMPUTER or name.endswith(".localhost") or name in _own_names():
        return THIS_COMPUTER
    if name in _METADATA_NAMES:
        return LINK_LOCAL
    ip = _ip(name)
    if ip is None:
        return ""
    if ip.is_loopback or ip.is_unspecified or (ip.version == 4 and ip.packed[0] == 0):
        return THIS_COMPUTER        # 0.0.0.0/8 and :: reach this computer too
    if ip.is_link_local or ip in _METADATA_IPS:
        return LINK_LOCAL
    return ""


def _home_kind(host: str) -> str:
    """HOME for one host as an address names it that is a device of the
    home network: a private or unique local address (not this computer's,
    not link-local - those are refused anyway), a name without a dot, or one
    with a home ending (.local, .lan, .home.arpa, .fritz.box...). Never
    looked up."""
    name = host.strip().lower().rstrip(".")
    if not name:
        return ""
    ip = _ip(name)
    from .home_net import HOME_SUFFIXES, is_home_client
    if ip is not None:
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            return ""
        # The home network as on_internet tells it: a private range or a
        # unique local address - the same rule both ways.
        return HOME if is_home_client(str(ip)) else ""
    if ":" in name or "%" in name:
        return ""
    if "." not in name or name.endswith(HOME_SUFFIXES) or name in _HOME_NAMES:
        return HOME
    return ""


def _hosts(address: str) -> set[str]:
    """Every host *address* may be read as naming: by each reader's end of
    the host's part (_HOST_ENDS), after its last "@", before its port; as
    written, percent-decoded and in Unicode's compatible form."""
    rest = address.partition("://")[2]
    found: set[str] = set()
    for ends in _HOST_ENDS:
        cut = min((i for i in map(rest.find, ends) if i >= 0), default=len(rest))
        part = rest[:cut].rpartition("@")[2]
        if part.startswith("["):
            close = part.find("]")
            found.add(part[1:close] if close > 0 else part[1:])
        else:
            found.update((part, part.partition(":")[0], part.rpartition(":")[0]))
    try:
        found.add(urlsplit(address).hostname or "")
    except ValueError:
        pass
    more = {unquote(h) for h in found}
    more |= {unicodedata.normalize("NFKC", h) for h in more}
    return {h for h in found | more if h}


def host_refusal(address: str, home: bool = False) -> str:
    """Why *address* names a host a source on the internet may not send the
    player to: THIS_COMPUTER (loopback however it is spelt, localhost, this
    computer's own name, 0.0.0.0) or LINK_LOCAL (169.254/16, fe80::/10, a
    cloud's metadata service), and with *home* HOME (a device of the home
    network: _home_kind); "" for any other host. Read as written, never
    looked up."""
    hosts = _hosts(address)
    kinds = {_host_kind(h) for h in hosts}
    for kind in (THIS_COMPUTER, LINK_LOCAL):
        if kind in kinds:
            return kind
    if home and any(_home_kind(h) for h in hosts):
        return HOME
    return ""


def home_allowed(options: Any) -> bool:
    """Whether a source's own *options* let its addresses lead into the home
    network (HOME_OPTION) - a list of the person's own on the internet."""
    value = options.get(HOME_OPTION) if isinstance(options, dict) else None
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


def acestream(address: str) -> bool:
    """Whether *address* is a stream of AceStream's engine on this computer
    (_ACESTREAM_NETLOCS, a path under /ace/: getstream, manifest.m3u8...).
    The whole host part must be one of those as written, so no reader can
    take another host or port from it."""
    try:
        parts = urlsplit(address)
    except ValueError:
        return False
    return (parts.scheme == "http" and parts.netloc.lower() in _ACESTREAM_NETLOCS
            and parts.path.startswith("/ace/"))


@functools.lru_cache(maxsize=4096)
def _head_refusal(head: str, home: bool = False) -> str:
    return host_refusal(head, home)


def picture_refusal(address: str, home: bool = False) -> str:
    """host_refusal for a picture's address from a source on the internet
    (a logo, a poster), the same rule as a stream's: THIS_COMPUTER,
    LINK_LOCAL, with *home* HOME, or "". Asked for every row of a list, so
    kept per scheme and host part - everything before the first "/" or "?"
    after "://", where every reader's host lies (_HOST_ENDS, urlsplit's)."""
    start = address.find("://")
    if start < 0:
        return ""
    ends = [i for i in (address.find("/", start + 3), address.find("?", start + 3)) if i >= 0]
    return _head_refusal(address[:min(ends)] if ends else address, home)


def on_internet(address: str) -> bool:
    """Whether a source whose own address (its playlist's, portal's or
    server's) is *address* is on the internet: its host neither an address
    of this computer or the home network (a private range, link-local,
    IPv6's unique local) nor a home network's name (core/home_net.py
    ``home_host``: localhost, a name without a dot, .local, .lan,
    .fritz.box...). Read as written, never looked up; an address whose host
    cannot be read is the internet's. False for no address at all."""
    text = (address or "").strip()
    if not text:
        return False
    if "://" not in text:
        text = "http://" + text
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return True
    if not host:
        return True
    from . import home_net
    ip = _ip(host)
    if ip is not None:
        return not home_net.is_home_client(str(ip))
    return not (home_net.home_host(host) or host.rstrip(".") in _HOME_NAMES)


#: Home routers' names for themselves outside home_net's endings (AVM's
#: Fritz!Box answers fritz.box, which its presets use).
_HOME_NAMES = frozenset(("fritz.box",))


def refusal(address: str, remote: bool = True, internet: bool = False,
            home_ok: bool = False) -> str:
    """Why *address* is not played, in a word for the log - "" when it is.
    *remote*: it came over the network (anything but a playlist file on
    this computer); *internet*: from a source on the internet
    (:func:`on_internet`), which hands out less - never a device of the home
    network either, unless *home_ok* (the source's HOME_OPTION)."""
    if not address:
        return ""
    if any(c < " " or c == "\x7f" for c in address):
        return "control characters"
    if address.lstrip().startswith("-"):
        return "an option"
    scheme = scheme_of(address)
    if scheme in NEVER:
        return f"{scheme}://"
    remote = remote or internet
    if scheme in _DEVICE_SCHEMES and (remote or _device(address) in _GRAPH_DEVICES):
        return f"{scheme}://{_device(address)}"
    if remote and scheme not in NETWORK:
        return f"{scheme}://" if scheme else "no network address"
    if not internet:
        return ""
    if scheme not in MEDIA:
        return f"{scheme}://{_INTERNET}"
    where = host_refusal(address, home=not home_ok)
    if where == THIS_COMPUTER and acestream(address):
        return ""
    if where == HOME and udpxy(address):
        return ""
    return where + _INTERNET if where else ""


#: A relay's address exactly as udpxy (and msd_lite, astra's relay) has it:
#: http(s), a home address as written, its port, ``/udp/`` or ``/rtp/``, a
#: multicast group with its port - and nothing else: no login, no other
#: path, no query, no fragment, no percent-escape, nothing a reader could
#: take another host or path from.
_RELAY = re.compile(
    r"(?i:https?)://(?P<host>[0-9]{1,3}(?:\.[0-9]{1,3}){3}|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?"
    r"/(?:udp|rtp)/@?(?P<group>[0-9]{1,3}(?:\.[0-9]{1,3}){3}|\[[0-9A-Fa-f:.]+\])"
    r"(?::[0-9]{1,5})?/?")


def udpxy(address: str) -> bool:
    """Whether *address* is a multicast group through a relay on the home
    network (udpxy on the router: http://192.168.1.1:4022/udp/239.0.0.1:1234)
    - what an operator's playlist on the internet names for its own
    channels. Only that shape (_RELAY): a private address of the home
    written as one, and the path a group and its port alone - so it asks
    the relay for a group and nothing else, never another page of the
    device that answers there (a router's own pages, ``/udp/x/../..``)."""
    m = _RELAY.fullmatch(address or "")
    if m is None:
        return False
    ip, gip = _ip(m.group("host")), _ip(m.group("group"))
    if ip is None or gip is None or not gip.is_multicast:
        return False
    return ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


#: Each refusal is said once a session: a list asks for its addresses often.
_said: set[tuple[str, str]] = set()
_SAID_MAX = 200


def played(address: object, remote: bool = True, what: str = "source",
           internet: bool = False, home_ok: bool = False) -> str:
    """*address* when a source's stream may be that, else "" - logged once,
    with the reason and never the address (it may carry a login)."""
    text = str(address or "")
    why = refusal(text, remote, internet, home_ok)
    if not why:
        return text
    key = (what, why)
    if key not in _said and len(_said) < _SAID_MAX:
        _said.add(key)
        if why == HOME + _INTERNET:
            log.warning("%s: a source on the internet named a device of the home network - "
                        "not played; if it is your own list, let it in: the source's settings "
                        "> Advanced > Addresses on my home network", what)
        elif why.endswith(_INTERNET):
            log.warning("%s: a source on the internet may not send the player to %s - "
                        "not played", what, why[:-len(_INTERNET)])
        else:
            log.warning("%s: an address that is no stream (%s) is not played", what, why)
    return ""
