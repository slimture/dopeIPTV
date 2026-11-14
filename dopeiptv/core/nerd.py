"""Nerd settings: what they ask of the player and the network.

Everything here is off until the user turns Nerd on (Settings > General >
Show Nerd settings, or seven taps on the version in About), and a setting
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

from . import hls_cap as _hls
from .log import log

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
BOX_KINDS = ("enigma2", "tvheadend", "hdhomerun")


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
        found = parse_ifconfig(_run(["/sbin/ifconfig"])) if sys.platform == "darwin" \
            else parse_ipconfig(_run(["ipconfig"]))
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


def is_lan_url(url: str) -> bool:
    """A stream on this computer or the home network (loopback, private and
    link-local addresses, .local names) - the VPN guard leaves those alone
    unless told to cover them."""
    import ipaddress
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").strip("[]")
    except ValueError:
        return False
    if not host:
        return not url.startswith(("http:", "https:", "rtmp:", "rtsp:", "udp:", "rtp:", "srt:"))
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def blocks_stream(url: str) -> bool:
    """The VPN guard says no to *url* (core code without a window asks this:
    recordings, multiview cells)."""
    try:
        return _current.vpn_blocks(local_source=is_lan_url(url))
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
        try:
            return int(float(self.get(key, str(default))))
        except ValueError:
            return default

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

    def core_options(self) -> list[tuple[str, str]]:
        """Options for a new mpv core, after the app's own: an mpv.conf of
        the user's, the typed raw options, then the Nerd page's own (which
        win)."""
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
        out += _hls.mpv_options(self.hls_cap())
        return out

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

    def stream_options(self, source: dict | None = None, url: str = "") -> dict[str, Any]:
        """Set before each stream opens: who the player says it is, extra
        headers, the proxy. With Nerd off: the app's own defaults, so a
        stream after turning it off is as before."""
        hdrs = self.headers(source)
        ref = hdrs.pop("Referer", "")
        return {"user-agent": self.user_agent(source),
                "http-header-fields": [f"{k}: {v}" for k, v in hdrs.items()],
                "referrer": ref,
                "http-proxy": self.proxy(source, url) if self.flag("nerd_proxy_player", True) else ""}

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
_ip_original: Any = None


def set_source(playlist: dict | None) -> None:
    """The source playing from now on: its own options and its kind."""
    global _source
    _source = (dict(playlist.get("options") or {}, _kind=playlist.get("kind") or "xtream")
               if playlist else None)


def source() -> dict | None:
    return _source


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


def apply_core(mpv: Any) -> list[str]:
    """The Nerd options and scripts for a newly made mpv core."""
    n = current()
    pairs = n.core_options()
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


def reapply_core(mpv: Any) -> dict[str, list[str]]:
    """Settings changed while *mpv* plays: an option no longer asked for goes
    back to what the core had, the rest are set anew - the picture ones at
    once, the stream ones (buffer, channel start) from the next stream.
    Scripts load when a core is made, so a new one waits for the next start."""
    pairs = current().core_options()
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


def apply_stream(mpv: Any, source: dict | None, was_applied: bool, url: str = "") -> bool:
    """Before a stream opens: user agent, headers and proxy for its source.
    Nothing at all while Nerd is off and never was this session; once it
    was, the defaults go back. Returns whether Nerd is in force."""
    n = current()
    if not n.on and not was_applied:
        return False
    for key, value in n.stream_options(source, url).items():
        try:
            mpv[key] = value
        except Exception as e:
            log.debug("nerd: %s not set: %s", key, e)
    return n.on
