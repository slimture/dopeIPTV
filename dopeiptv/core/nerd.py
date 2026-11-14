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
from typing import Any, Protocol

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


def vpn_interfaces(names: list[str] | None = None) -> list[str]:
    """Network interfaces that look like a VPN and are up."""
    if names is None:
        try:
            names = [n for _i, n in socket.if_nameindex()]
        except (OSError, AttributeError):
            names = []
    return [n for n in names if VPN_PATTERNS.match(n) and _is_up(n)]


def _is_up(name: str) -> bool:
    """Up, as far as this system tells: Linux says it in sysfs (a WireGuard
    interface reads "unknown" when up); elsewhere one that exists counts."""
    if sys.platform.startswith("linux"):
        try:
            with open(f"/sys/class/net/{name}/operstate", encoding="ascii") as fh:
                return fh.read().strip() != "down"
        except OSError:
            return False
    return True


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
        return out

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
            try:
                names = [n for _i, n in socket.if_nameindex()]
            except (OSError, AttributeError):
                names = []
            return name not in names or not _is_up(name)
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
    if _current.on and _current.get("nerd_log_level") == "debug" and not os.environ.get("DOPEIPTV_LOG"):
        import logging
        log.setLevel(logging.DEBUG)
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


def apply_core(mpv: Any) -> list[str]:
    """The Nerd options and scripts for a newly made mpv core."""
    n = current()
    took = apply_options(mpv, n.core_options())
    for path in n.scripts():
        try:
            mpv.command("load-script", path)
            took.append(f"script:{os.path.basename(path)}")
        except Exception as e:
            log.info("nerd: script %s not loaded: %s", path, e)
    if took:
        log.info("nerd: %d option(s) on the player: %s", len(took), ", ".join(took))
    return took


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
