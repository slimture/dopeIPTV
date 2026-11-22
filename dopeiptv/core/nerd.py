"""Nerd settings: what they ask of the player and the network.

Everything here is off until the user turns Nerd on (Settings > Nerd >
Status, or seven taps on the version in About), and a setting
left empty changes nothing: with Nerd off the app plays exactly as it does
without this module. The window installs a profile (``install``) at start
and after each Settings change; the player, the provider clients and the
rewind buffer ask it what to do.

Options that break the embedded player (another video output, a window of
mpv's own) are refused, whatever the user types; every other one is applied
one at a time and skipped, not fatal, when this libmpv does not know it.
"""

from __future__ import annotations

import os
import re
import socket
import sys
import weakref
from typing import Any, Protocol
from urllib.parse import urlsplit

from . import hls_cap as _hls
from .log import log
from .num import whole

DEFAULT_UA = "dopeIPTV/1.0"

# User agents panels and portals are known to want, by name.
UA_PRESETS: dict[str, str] = {
    "vlc": "VLC/3.0.21 LibVLC/3.0.21",
    "kodi": "Kodi/21.1 (X11; Linux x86_64) App_Bitness/64 Version/21.1-(21.1.0)-Git:20240804",
    "tivimate": "TiviMate/5.1.6 (Android 12)",
    "smarters": "IPTVSmartersPlayer",
    "ffmpeg": "Lavf/60.16.100",
    "mag": ("Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) "
            "MAG200 stbapp ver: 2 rev: 250 Safari/533.3"),
    "enigma2": "Enigma2 HbbTV/1.1.1 (+PVR+RTSP+DL;OpenATV;;;)",
    "chrome": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/131.0.0.0 Safari/537.36"),
}

# Passthrough formats mpv's audio-spdif knows.
SPDIF_CODECS = ("ac3", "eac3", "dts", "dts-hd", "truehd")

# mpv options that would take the picture out of the window (or the window
# out of the app): never applied from the raw options or an mpv.conf.
REFUSED = frozenset({"vo", "wid", "force-window", "gpu-context", "terminal",
                     "input-ipc-server", "idle", "config", "config-dir",
                     "include", "profile", "player-operation-mode"})

# Interfaces a VPN shows up as, where none is named.
VPN_PATTERNS = re.compile(r"^(wg|tun|tap|ppp|ipsec|tailscale|nordlynx|proton|mullvad|utun|zt)",
                          re.I)

_TRUE = ("1", "true", "yes", "on")
# Sources on the home network: no proxy unless one is named for them.
BOX_KINDS = ("enigma2", "tvheadend", "hdhomerun", "vdr", "dvbviewer", "nextpvr", "mythtv", "satip")
# Media servers: the same while their address is on the home network.
MEDIA_KINDS = ("jellyfin", "emby", "plex")


def _is_loopback(url: str) -> bool:
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower() if "://" in (url or "") else ""
    return host in ("localhost", "::1") or host.startswith("127.")


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


def parse_mpv_options(text: str) -> list[tuple[str, str]]:
    """key=value lines (mpv.conf's own form; a leading -- is fine, a bare
    key means yes, # starts a comment). A [profile] section is skipped:
    only the options outside any profile apply. Refused keys are left out."""
    out: list[tuple[str, str]] = []
    in_profile = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_profile = line.lower() not in ("[default]",)
            continue
        if in_profile:
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()
        line = line.lstrip("-")
        key, sep, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", key):
            continue
        if key.replace("_", "-") in REFUSED:
            log.info("nerd: mpv option %r is not allowed here", key)
            continue
        out.append((key.replace("_", "-"), value if sep else "yes"))
    return out


def parse_headers(text: str) -> dict[str, str]:
    """'Name: value' lines (or Name=value); # starts a comment."""
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9!#$%&'*+.^_`|~-]+)\s*[:=]\s*(.*)$", line)
        if m and m.group(2).strip() and "\r" not in line and "\n" not in line:
            out[m.group(1)] = m.group(2).strip()
    return out


# Windows names a VPN adapter after its software.
_WIN_VPN = re.compile(r"wireguard|tailscale|openvpn|tap-windows|wintun|nordlynx|proton|mullvad|"
                      r"cisco anyconnect|fortinet|globalprotect|zerotier|\bwg\d", re.I)


def parse_ifconfig(text: str) -> list[str]:
    """macOS: the tunnels (utun, ipsec, ppp, tun) that are up with an IPv4
    address. macOS keeps several utun interfaces of its own (iCloud and
    others) with link-local IPv6 only, so a name alone says nothing."""
    out, name, up, v4 = [], "", False, False
    for line in (text or "").splitlines() + ["end: flags=0<>"]:
        m = re.match(r"^([A-Za-z0-9.]+): flags=\w+<([^>]*)>", line)
        if m:
            if name and up and v4 and re.match(r"(utun|ipsec|ppp|tun|tap|wg)\d*", name):
                out.append(name)
            name, up, v4 = m.group(1), "UP" in m.group(2).split(","), False
        elif re.match(r"^\s+inet \d", line):
            v4 = True
    return out


def parse_ipconfig(text: str) -> list[str]:
    """Windows: the VPN adapters that have an IPv4 address."""
    out, name, v4 = [], "", False
    for line in (text or "").splitlines() + ["End adapter end:"]:
        m = re.match(r"^\S.*? adapter (.+):\s*$", line) or re.match(r"^(\S.*):\s*$", line)
        if m and not line.startswith(" "):
            if name and v4 and _WIN_VPN.search(name):
                out.append(name)
            name, v4 = m.group(1).strip(), False
        elif "IPv4" in line and re.search(r"\d+\.\d+\.\d+\.\d+", line):
            v4 = True
    return out


def _run(cmd: list[str]) -> str:
    import subprocess

    from .player_exec import NO_WINDOW
    try:
        # NO_WINDOW: the windowed Windows build flashed a console otherwise.
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                              errors="replace", creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


#: How long an answer from ifconfig/ipconfig holds: a playback start and the
#: stats overlay's once-a-second line ask far more often than tunnels change.
TUNNELS_FOR = 5.0
_tunnels_seen: tuple[float, list[str]] = (-1e9, [])


def active_tunnels() -> list[str]:
    """The VPN-like interfaces that are up and carry traffic, as this
    system can tell: Linux by sysfs, macOS by ifconfig, Windows by
    ipconfig (those two remembered for a few seconds)."""
    global _tunnels_seen
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        import time
        now = time.monotonic()
        if now - _tunnels_seen[0] < TUNNELS_FOR:
            return list(_tunnels_seen[1])
        from .player_exec import system_program
        found = parse_ifconfig(_run(["/sbin/ifconfig"])) if sys.platform == "darwin" \
            else parse_ipconfig(_run([system_program("ipconfig.exe")]))
        _tunnels_seen = (now, found)
        return list(found)
    try:
        names = [n for _i, n in socket.if_nameindex()]
    except (OSError, AttributeError):
        names = []
    return [n for n in names if VPN_PATTERNS.match(n) and _is_up(n)]


def vpn_interfaces(names: list[str] | None = None) -> list[str]:
    """Network interfaces that look like a VPN and are up (*names*: check
    these instead of asking the system - Linux's way)."""
    if names is None:
        return active_tunnels()
    return [n for n in names if VPN_PATTERNS.match(n) and _is_up(n)]


#: Names only a home network has: a router's own (fritz.box), and the
#: endings home routers and mDNS give their machines.
HOME_SUFFIXES = (".local", ".lan", ".home", ".internal", ".home.arpa", ".localdomain")
HOME_NAMES = ("localhost", "fritz.box")


def ipv4_any(text: str) -> Any:
    """*text* as an IPv4 address in every spelling the system's resolver
    takes (inet_aton): four parts, or fewer with the last filling the rest
    ("10.1" is 10.0.0.1), each decimal, octal with a leading 0 or hex with
    0x - "134744072", "0x08080808" and "010.010.010.010" are all 8.8.8.8.
    None for anything else."""
    import ipaddress
    parts = text.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    nums = []
    for part in parts:
        p = part.lower()
        try:
            if p.startswith("0x"):
                if not p[2:] or len(p) > 10:
                    return None
                nums.append(int(p[2:], 16))
            elif len(p) > 1 and p.startswith("0"):
                if len(p) > 12:
                    return None
                nums.append(int(p[1:], 8))
            elif p.isascii() and p.isdigit() and len(p) <= 10:
                nums.append(int(p))
            else:
                return None
        except ValueError:
            return None
    last_bits = 8 * (5 - len(nums))
    if any(n > 255 for n in nums[:-1]) or nums[-1] >= 1 << last_bits:
        return None
    value = 0
    for n in nums[:-1]:
        value = value << 8 | n
    return ipaddress.IPv4Address(value << last_bits | nums[-1])


def is_home_host(host: str) -> bool:
    """A host on this computer or the home network: a loopback, private or
    link-local address - in whatever spelling the system takes it, judged
    by the address it reaches (ipv4_any; an IPv4-mapped IPv6 one by its
    IPv4) -, a name without a dot (a box called "vuduo2"), or a name only
    a home network has (.local, .lan, .home, .internal, .home.arpa,
    fritz.box). One rule for the VPN guard and for the events sent to a
    home system."""
    import ipaddress
    h = (host or "").strip().strip("[]").rstrip(".").lower()
    if not h:
        return False
    ip: Any
    try:
        ip = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        ip = ipv4_any(h)
        if ip is None:
            return h in HOME_NAMES or h.endswith(HOME_SUFFIXES) or "." not in h
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


# A scheme ("udp:@239.0.0.1:1234", "smb://", "rtmpt:..."): two letters at
# least, so a Windows drive ("C:\\...") is a path.
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")


def _network_host(url: str) -> str | None:
    """The server of a path or file: address on another computer - a
    Windows network path (\\\\server\\share, //server/share,
    \\\\?\\UNC\\server\\share) or file://server/... -, else None."""
    from urllib.parse import unquote
    u = (url or "").strip()
    if u.lower().startswith("file:"):
        try:
            parts = urlsplit(u)
        except ValueError:
            return ""
        host = parts.netloc.lower()
        if host not in ("", "localhost") and not re.fullmatch(r"[a-z]:", host):
            return parts.hostname or host
        u = unquote(parts.path)
    s = u.replace("\\", "/")
    if not s.startswith("//"):
        return None
    rest = s[2:]
    if rest[:2] in ("?/", "./"):
        if not rest[2:].upper().startswith("UNC/"):
            return None                     # \\?\C:\... is this computer's
        rest = rest[6:]
    return rest.split("/", 1)[0]


def is_own_file(url: str) -> bool:
    """A file on this computer - the user's own recording or video, never a
    stream from anywhere: a file: address or a path, never one on another
    computer (a Windows network path, file://server/...)."""
    u = (url or "").strip()
    if not u:
        return False
    if not u.lower().startswith("file:") and _SCHEME.match(u):
        return False
    return _network_host(u) is None


def is_lan_url(url: str) -> bool:
    """A stream on this computer or the home network (is_home_host), or a
    file of its own - the VPN guard leaves those alone unless told to cover
    the home network too (a file never is: blocks_stream)."""
    if is_own_file(url):
        return True
    host = _network_host(url)
    if host is None:
        try:
            host = urlsplit(url or "").hostname or ""
        except ValueError:
            return False
    return is_home_host(host)


def is_local_stream(url: str, source_kind: str | None = None) -> bool:
    """On the home network as the VPN guard counts it: a box's source (an
    Enigma2 receiver, a Tvheadend, VDR, DVBViewer, NextPVR, MythTV or SAT>IP
    server, an HDHomeRun) whatever its address, or any source's stream at a
    home address."""
    return (source_kind or "") in BOX_KINDS or is_lan_url(url)


def blocks_stream(url: str, source_kind: str | None = None) -> bool:
    """The VPN guard says no to *url* - the one rule the window, recordings
    and multiview cells ask. The user's own files always play; a stream on
    the home network (is_local_stream) only while the guard covers it too."""
    if is_own_file(url):
        return False
    try:
        return _current.vpn_blocks(local_source=is_local_stream(url, source_kind))
    except Exception:
        log.debug("vpn guard check", exc_info=True)
        return False


def _is_up(name: str) -> bool:
    """Linux: up as sysfs says (a WireGuard interface reads "unknown" when
    up)."""
    try:
        with open(f"/sys/class/net/{name}/operstate", encoding="ascii") as fh:
            return fh.read().strip() != "down"
    except OSError:
        return False


class Nerd:
    """A snapshot of the Nerd settings."""

    def __init__(self, settings: Settings | None) -> None:
        self.s = settings

    def get(self, key: str, default: str = "") -> str:
        if self.s is None:
            return default
        v = self.s.value(key, default)
        return default if v is None else str(v)

    def flag(self, key: str, default: bool = False) -> bool:
        return self.get(key, "true" if default else "false").strip().lower() in _TRUE

    def num(self, key: str, default: int) -> int:
        # A spin box's range: Qt refuses an int past 32 bits.
        return whole(self.get(key, str(default)), default, lo=-(2**31), hi=2**31 - 1)

    @property
    def on(self) -> bool:
        return self.flag("nerd_enabled")

    # -- network --------------------------------------------------------------

    def user_agent(self, source: dict | None = None) -> str:
        src = (source or {}).get("user_agent") if self.on else None
        ua = str(src or (self.get("nerd_user_agent") if self.on else "")).strip()
        return UA_PRESETS.get(ua, ua) or DEFAULT_UA

    def headers(self, source: dict | None = None) -> dict[str, str]:
        if not self.on:
            return {}
        out = parse_headers(self.get("nerd_headers"))
        ref = self.get("nerd_referer").strip()
        src = source or {}
        out.update(parse_headers(str(src.get("headers") or "")))
        ref = str(src.get("referer") or ref).strip()
        if ref:
            out["Referer"] = ref
        return out

    def proxy(self, source: dict | None = None, url: str = "") -> str:
        """The proxy for this source: its own, none ("direct"), or the
        global one. Never for the machine itself (the rewind buffer's own
        server) and, unless a box's source names one, never for a box on
        the home network."""
        if not self.on or _is_loopback(url):
            return ""
        src = str((source or {}).get("proxy") or "").strip()
        if src.lower() in ("direct", "none", "off"):
            return ""
        if not src and (source or {}).get("_kind") in BOX_KINDS:
            return ""
        # A media server is a box when it is on the home network (Jellyfin
        # in the cupboard), a provider when it is not (Plex's relay).
        if not src and (source or {}).get("_kind") in MEDIA_KINDS and is_lan_url(
                url or str((source or {}).get("_server") or "")):
            return ""
        return src or self.get("nerd_proxy").strip()

    def apply_session(self, session: Any, source: dict | None = None) -> None:
        """A provider client's requests session: user agent, headers, proxy."""
        if not self.on:
            return
        ua = self.user_agent(source)
        # A portal checks for a MAG box's agent: only a source's own setting
        # replaces the one the Stalker client sends.
        own = (source or {}).get("user_agent")
        if ua != DEFAULT_UA and ((source or {}).get("_kind") != "stalker" or own):
            session.headers["User-Agent"] = ua
        session.headers.update(self.headers(source))
        px = self.proxy(source)
        if px:
            session.proxies.update({"http": px, "https": px})

    # -- the player -----------------------------------------------------------

    def core_options(self, picker: bool = True) -> list[tuple[str, str]]:
        """Options for a new mpv core, after the app's own: an mpv.conf of
        the user's, the typed raw options, then the Nerd page's own (which
        win). *picker*: the core switches an adaptive stream's variant
        itself (hls_cap.mpv_options)."""
        if not self.on:
            return []
        out: list[tuple[str, str]] = []
        conf = self.get("nerd_mpv_config_dir").strip()
        if conf:
            path = os.path.join(os.path.expanduser(conf), "mpv.conf")
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    out += parse_mpv_options(fh.read())
            except OSError as e:
                log.info("nerd: no mpv.conf at %s (%s)", path, e)
        out += parse_mpv_options(self.get("nerd_mpv_options"))
        timeout = self.num("nerd_net_timeout", 0)
        if timeout > 0:
            out.append(("network-timeout", str(timeout)))
        recon = self.num("nerd_reconnect_max", 0)
        if recon > 0:
            out.append(("demuxer-lavf-o", "reconnect=1,reconnect_streamed=1,"
                        f"reconnect_on_network_error=1,reconnect_delay_max={recon}"))
        spdif = [c for c in self.get("nerd_spdif").split(",") if c.strip() in SPDIF_CODECS]
        if spdif:
            out.append(("audio-spdif", ",".join(c.strip() for c in spdif)))
        shaders = [p.strip() for p in re.split(r"[\n;]", self.get("nerd_shaders")) if p.strip()]
        if shaders:
            out.append(("glsl-shaders", os.pathsep.join(os.path.expanduser(p) for p in shaders)))
        sync = self.get("nerd_video_sync").strip()
        if sync:
            out.append(("video-sync", sync))
        if self.flag("nerd_interpolation"):
            out.append(("interpolation", "yes"))
            out.append(("tscale", "oversample"))
        mb = self.num("nerd_demux_max_mb", 0)
        if mb > 0:
            out.append(("demuxer-max-bytes", f"{mb}MiB"))
        start = self.get("nerd_probe", "")
        if start == "thorough":
            out += [("demuxer-lavf-analyzeduration", "5"), ("demuxer-lavf-probesize", "5000000")]
        elif start == "instant":
            out += [("demuxer-lavf-analyzeduration", "0.5"), ("demuxer-lavf-probesize", "500000")]
        out += _hls.mpv_options(self.hls_cap(), picker)
        if self.strict_tls() and not any(k == "tls-verify" for k, _v in out):
            out += strict_tls_mpv()
        return out

    def strict_tls(self) -> bool:
        """Nerd's "Check every stream's certificate" (STRICT_TLS): mpv and
        the recorder check an https stream's certificate against the
        authorities requests trusts (certifi) - off by default, since many
        providers' certificates do not check out. Only while Nerd is on."""
        return self.on and self.flag(STRICT_TLS)

    def typed_tls(self) -> bool:
        """Whether the person's own mpv options or mpv.conf set tls-verify:
        theirs, left alone - Nerd's strict check and a pinned stream's alike."""
        if not self.on:
            return False
        typed = parse_mpv_options(self.get("nerd_mpv_options"))
        conf = self.get("nerd_mpv_config_dir").strip()
        if conf:
            try:
                with open(os.path.join(os.path.expanduser(conf), "mpv.conf"),
                          encoding="utf-8", errors="replace") as fh:
                    typed += parse_mpv_options(fh.read())
            except OSError:
                pass
        return any(k == "tls-verify" for k, _v in typed)

    def hls_cap(self) -> _hls.Cap:
        """The highest quality adaptive streams play at (core/hls_cap.py):
        none while Nerd is off or the page says Best."""
        if not self.on:
            return _hls.Cap()
        return _hls.cap_of(self.get("nerd_hls_quality"), self.num("nerd_hls_max_kbps", 0))

    def scripts(self) -> list[str]:
        """Lua/JS scripts from the user's mpv folder (scripts/)."""
        conf = self.get("nerd_mpv_config_dir").strip() if self.on else ""
        if not conf:
            return []
        d = os.path.join(os.path.expanduser(conf), "scripts")
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return []
        return [os.path.join(d, n) for n in names if n.endswith((".lua", ".js"))]

    def stream_options(self, source: dict | None = None, url: str = "",
                       opened: str = "") -> dict[str, Any]:
        """Set before each stream opens: who the player says it is, extra
        headers, the proxy. With Nerd off: the app's own defaults, so a
        stream after turning it off is as before. *opened*: the address mpv
        opens when it is not *url* (the rewind buffer's, on this computer)."""
        hdrs = self.headers(source)
        ref = hdrs.pop("Referer", "")
        px, _pinned = self.player_route(source, url, opened)
        return {"user-agent": self.user_agent(source),
                "http-header-fields": [f"{k}: {v}" for k, v in hdrs.items()],
                "referrer": ref,
                "http-proxy": px}

    def player_route(self, source: dict | None = None, url: str = "",
                     opened: str = "") -> tuple[str, bool]:
        """(the proxy the player takes a stream through, whether that is the
        bridge checking a certificate the source pins for it)."""
        target = opened or url
        # Never the proxy for the rewind buffer's own server on this
        # computer (its ingest takes the provider through it): mpv sent the
        # loopback address, token and all, to the proxy, which cannot
        # reach it.
        px = (self.proxy(source, url)
              if self.flag("nerd_proxy_player", True) and not _is_loopback(target) else "")
        if px or not self.on:
            return px, False
        # The source's own DNS, IP version or pinned certificate: the
        # player through the bridge (core/net_bridge.py) - never where a
        # proxy takes the stream, whose DNS that is.
        from .net_bridge import address_for, checks_pin
        px = address_for(source, target, self)
        return px, bool(px) and checks_pin(source, target, self)

    # -- the VPN guard --------------------------------------------------------

    def vpn_blocks(self, local_source: bool = False) -> bool:
        """True when the guard is on and no VPN is up: nothing plays. A box
        on the home network plays anyway unless the guard covers it too."""
        if not (self.on and self.flag("nerd_vpn_guard")):
            return False
        if local_source and not self.flag("nerd_vpn_lan"):
            return False
        name = self.get("nerd_vpn_iface").strip()
        if name:
            if sys.platform.startswith("linux"):
                return not _is_up(name)
            return not any(name.lower() in t.lower() for t in active_tunnels())
        return not vpn_interfaces()


# -- the profile in use -------------------------------------------------------

_current = Nerd(None)
_source: dict | None = None
_cast_source: dict | None = None
_ip_original: Any = None


def _options_of(playlist: dict | None) -> dict | None:
    return (dict(playlist.get("options") or {}, _kind=playlist.get("kind") or "xtream")
            if playlist else None)


def set_source(playlist: dict | None) -> None:
    """The source playing from now on: its own options and its kind."""
    global _source
    _source = _options_of(playlist)


def source() -> dict | None:
    return _source


def set_cast_source(playlist: dict | None, outside: bool = False) -> None:
    """The source a cast starting now is from (what plays from another
    source than the one in use - started before a switch, by failover or
    from the Search page - keeps its own); None: the source playing.
    *outside*: an address from outside (``same_server``), no source's."""
    global _cast_source
    _cast_source = {} if outside else _options_of(playlist)


def same_server(url: str, server: str) -> bool:
    """Whether *url* is on the host of a source's *server* (the address it
    was added with). An address from outside the source's lists - a
    dopeiptv:// link, --play, the remote API, Open address… - gets that
    source's user agent, headers, referer, proxy and route only then: they
    often hold the provider's token, which another host must never see."""
    try:
        a = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
        b = (urlsplit(str(server or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return bool(a) and a == b


def cast_source() -> dict | None:
    """The source of the stream on a TV, Kodi or an Apple TV, in source()'s
    form: whose user agent, headers and proxy its receiver takes."""
    return _cast_source if _cast_source is not None else _source


def install(settings: Settings | None) -> Nerd:
    """The profile the app uses from now on (at start, after Settings)."""
    global _current
    _current = Nerd(settings)
    apply_ip_family(_current.get("nerd_ip_family", "auto") if _current.on else "auto")
    if not os.environ.get("DOPEIPTV_LOG"):
        import logging
        debug = _current.on and _current.get("nerd_log_level") == "debug"
        log.setLevel(logging.DEBUG if debug else logging.INFO)
    from .log import set_file_logging
    set_file_logging(_current.on and _current.flag("nerd_log_file"))
    # The picon folder is where pictures of the person's own are: the image
    # loader reads a file as a picture only from such a folder.
    from . import picture_files
    folder = _current.get("nerd_picon_dir").strip() if _current.on else ""
    picture_files.trust("picons", [folder] if folder else [])
    return _current


def current() -> Nerd:
    return _current


def apply_ip_family(mode: str) -> None:
    """IPv4 or IPv6 only for the app's own connections (lists, guides,
    artwork); "auto" lets the system choose. The player follows the system."""
    global _ip_original
    try:
        from urllib3.util import connection
    except ImportError:
        return
    if _ip_original is None:
        _ip_original = connection.allowed_gai_family
    if mode == "ipv4":
        connection.allowed_gai_family = lambda: socket.AF_INET
    elif mode == "ipv6" and socket.has_ipv6:
        connection.allowed_gai_family = lambda: socket.AF_INET6
    else:
        connection.allowed_gai_family = _ip_original


# -- applying to an mpv core (the player and the tests use these) -------------

def apply_options(mpv: Any, pairs: list[tuple[str, str]]) -> list[str]:
    """Set each option on *mpv*, skipping (and logging) one this libmpv
    rejects. Returns the keys that took."""
    took: list[str] = []
    for key, value in pairs:
        try:
            mpv[key] = value
            took.append(key)
        except Exception as e:
            log.info("nerd: mpv option %s=%r skipped: %s", key, value, e)
    return took


# What each core had before Nerd set an option on it, put back when the
# option goes again (Settings changed while it plays). A value libmpv will
# not take back (0 for "not set") is replaced by what that means.
_before: dict[int, dict[str, Any]] = {}
_UNSET_MEANS = {"demuxer-lavf-probesize": "5000000"}
UNSET_MEANS = _UNSET_MEANS


def _remember(mpv: Any, keys: list[str]) -> dict[str, Any]:
    saved = _before.get(id(mpv))
    if saved is None:
        saved = _before[id(mpv)] = {}
        try:
            weakref.finalize(mpv, _before.pop, id(mpv), None)
        except TypeError:
            pass
    for key in keys:
        if key not in saved:
            try:
                saved[key] = mpv[key]
            except Exception:
                saved[key] = None
    return saved


def remember(mpv: Any, keys: list[str]) -> dict[str, Any]:
    """Note what *mpv* has for *keys* before Nerd first sets them (a
    per-stream overlay, core/live_latency.py, shares this with the core's
    options: one record of what the core had)."""
    return _remember(mpv, keys)


def remembered(mpv: Any) -> dict[str, Any]:
    """What *mpv* had before Nerd set each key (the record itself)."""
    return _before.get(id(mpv), {})


def apply_core(mpv: Any, picker: bool = True) -> list[str]:
    """The Nerd options and scripts for a newly made mpv core. *picker*:
    the core has the player's switch to an adaptive stream's variant
    (core/hls_cap.py) - a multiview cell has none."""
    n = current()
    pairs = n.core_options(picker)
    _remember(mpv, [k for k, _v in pairs])
    took = apply_options(mpv, pairs)
    for path in n.scripts():
        try:
            mpv.command("load-script", path)
            took.append(f"script:{os.path.basename(path)}")
        except Exception as e:
            log.info("nerd: script %s not loaded: %s", path, e)
    if took:
        log.info("nerd: %d option(s) on the player: %s", len(took), ", ".join(took))
    return took


def reapply_core(mpv: Any, picker: bool = True) -> dict[str, list[str]]:
    """Settings changed while *mpv* plays: an option no longer asked for goes
    back to what the core had, the rest are set anew - the picture ones at
    once, the stream ones (buffer, channel start) from the next stream.
    Scripts load when a core is made, so a new one waits for the next start.
    *picker* as for apply_core: the caller arms its switch for the new cap
    too (EmbeddedPlayer.nerd_reapply), or a reconnect before the next start
    would stay on the smallest variant."""
    pairs = current().core_options(picker)
    keys = [k for k, _v in pairs]
    saved = _remember(mpv, keys)
    restored: list[str] = []
    for key in [k for k in saved if k not in keys]:
        value = saved.pop(key)
        if value is None:
            continue
        try:
            mpv[key] = value
        except Exception:
            try:
                mpv[key] = _UNSET_MEANS[key]
            except Exception as e:
                log.info("nerd: %s not put back: %s", key, e)
                continue
        restored.append(key)
    took = apply_options(mpv, pairs)
    if restored or took:
        log.info("nerd: player updated (set %s; back to default %s)",
                 ", ".join(took) or "-", ", ".join(restored) or "-")
    return {"set": took, "restored": restored}


def apply_stream(mpv: Any, source: dict | None, was_applied: bool, url: str = "",
                 opened: str = "") -> bool:
    """Before a stream opens: user agent, headers and proxy for its source.
    Nothing at all while Nerd is off and never was this session; once it
    was, the defaults go back. Returns whether Nerd is in force. *opened*:
    the address mpv opens, when not *url* (the rewind buffer's)."""
    n = current()
    pinned = False
    if n.on or was_applied:
        for key, value in n.stream_options(source, url, opened).items():
            try:
                mpv[key] = value
            except Exception as e:
                log.debug("nerd: %s not set: %s", key, e)
        pinned = n.player_route(source, url, opened)[1]
    # The certificate, for everyone, whether Nerd is on or not.
    _apply_tls(mpv, source, opened or url, pinned, n)
    return n.on


#: Nerd: check every https stream's certificate in the player and the
#: recorder - a server at home's too (the setting; off by default). A
#: stream from the internet is checked whatever it says (checks_certificate).
STRICT_TLS = "nerd_tls_strict"
#: A source's own option: its streams play without their certificate
#: checked - the person's answer when one did not check out (asked once,
#: the window's _tls_offer), and theirs to take back on its page.
TLS_UNCHECKED = "tls_unchecked"


def tls_unchecked(source: dict | None) -> bool:
    return str((source or {}).get(TLS_UNCHECKED) or "").lower() in ("1", "true", "yes")


def checks_certificate(source: dict | None, url: str, n: "Nerd | None" = None) -> bool:
    """Whether the player and the recorder check *url*'s certificate: an
    https stream on the internet, as the app's own requests always are -
    a provider taken over on the way, or a network that pretends to be
    it, then gets nothing, the login in an Xtream address least of all.
    Not for a source the person let play unchecked (TLS_UNCHECKED), and
    for a server at home only with Nerd's strict check (its certificate is
    often its own). A host whose certificate the source pins is the
    bridge's to check (_apply_tls)."""
    n = n or current()
    text = str(url or "")
    if not text.lower().startswith("https://") or tls_unchecked(source):
        return False
    if n.strict_tls():
        return True
    from urllib.parse import urlsplit

    from .dest_guard import named_kind
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return False
    return bool(host) and named_kind(host) == ""


def ca_file() -> str:
    """The authorities the app's own requests trust (certifi's bundle, the
    frozen builds carry it), for mpv and FFmpeg to check against; "" when
    it cannot be found - then the libraries' own default."""
    try:
        import certifi
        path = certifi.where()
    except Exception:
        return ""
    return path if path and os.path.isfile(path) else ""


def strict_tls_mpv() -> list[tuple[str, str]]:
    """mpv's options for a checked certificate."""
    ca = ca_file()
    return [("tls-verify", "yes"), *([("tls-ca-file", ca)] if ca else [])]


def strict_tls_recorder(kind: str, url: str = "", source: dict | None = None) -> list[str]:
    """The recorder's (ffmpeg or mpv) arguments for *url*'s certificate, as
    the player checks it (checks_certificate): checked, or - for a source
    the person let play unchecked - not checked, said outright (a newer
    mpv checks by default). [] when it changes nothing, and for ffmpeg
    always for an address that is not https (an input option no protocol
    takes stops ffmpeg)."""
    https = str(url or "").lower().startswith("https://")
    if kind == "ffmpeg" and not https:
        return []
    if checks_certificate(source, url):
        ca = ca_file()
        if kind == "ffmpeg":
            return ["-tls_verify", "1", *(["-ca_file", ca] if ca else [])]
        return ["--tls-verify=yes", *([f"--tls-ca-file={ca}"] if ca else [])]
    if https and tls_unchecked(source):
        return ["-tls_verify", "0"] if kind == "ffmpeg" else ["--tls-verify=no"]
    return []


# What tls-verify and tls-ca-file each core had of its own, before a stream
# set them (_apply_tls).
_tls_own: dict[int, Any] = {}
_tls_watched: set[int] = set()


def _forget_tls(key: int) -> None:
    _tls_own.pop(key, None)
    _tls_watched.discard(key)


def _apply_tls(mpv: Any, source: dict | None, address: str, pinned: bool, n: "Nerd") -> None:
    """tls-verify (and the authorities to check against) for the stream mpv
    opens next at *address*: checked where checks_certificate says; off for
    a host whose certificate the source pins - the bridge checks it against
    the pin before the tunnel opens (core/net_bridge.py), a pinned home
    server's certificate signed by no authority mpv knows - and for a
    source the person let play unchecked; else the core's own setting
    (0.37 checks nothing by default, a newer mpv everything). A tls-verify
    typed in Nerd's mpv options is the person's and left alone."""
    if n.typed_tls():
        return
    key = id(mpv)
    if key not in _tls_own:
        try:
            _tls_own[key] = (mpv["tls-verify"], mpv["tls-ca-file"])
        except Exception as e:
            log.debug("nerd: tls-verify not read: %s", e)
            return
        if key not in _tls_watched:
            try:
                weakref.finalize(mpv, _forget_tls, key)
                _tls_watched.add(key)
            except TypeError:
                pass
    own_verify, own_ca = _tls_own[key]
    https = str(address).lower().startswith("https://")
    if pinned or (https and tls_unchecked(source)):
        verify, ca = False, own_ca
    elif checks_certificate(source, address, n):
        verify, ca = True, (ca_file() or own_ca)
    else:
        verify, ca = own_verify, own_ca
    for prop, value in (("tls-verify", verify), ("tls-ca-file", ca)):
        try:
            mpv[prop] = value
        except Exception as e:
            log.debug("nerd: %s not set: %s", prop, e)
