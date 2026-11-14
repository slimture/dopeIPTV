"""Does a Nerd setting work? Each check tries the setting for real and says
so: ok, warn or fail, a line for the page (an i18n key and its values) and
the details for the problem window - what was tried and the exact error,
never a password, token or MAC address.

    proxy, user agent, headers  a request to the active source through them
    IP version                  the source's host over IPv4 / IPv6
    udpxy                       its status page
    picon folder                the picons in it, and the channels they match
    mpv folder                  its mpv.conf and scripts
    shaders                     the files exist
    mpv options                 each one set on a real, throwaway mpv core

Every check runs off the window's thread (they wait on the network).
"""

from __future__ import annotations

import os
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from . import nerd
from .log import log

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
MASK = "•••"
# Query keys and header names whose values are never shown.
_SECRET_KEYS = {"username", "password", "pass", "user", "token", "key", "apikey",
                "api_key", "mac", "auth", "sid", "session", "deviceauth", "secret"}
_SECRET_HEADERS = ("authorization", "cookie", "token", "key", "secret", "auth")


@dataclass
class Check:
    state: str
    key: str
    params: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.state == FAIL


# -- keeping secrets out ---------------------------------------------------

def redact_url(url: str) -> str:
    """*url* with its login, secret query values and Xtream path login
    (/live/user/pass/...) masked."""
    try:
        u = urlsplit(url or "")
    except ValueError:
        return MASK
    netloc = u.netloc
    if "@" in netloc:
        netloc = MASK + "@" + netloc.rsplit("@", 1)[1]
    q = "&".join(f"{quote(k)}={MASK if k.lower() in _SECRET_KEYS else quote(v)}"
                 for k, v in parse_qsl(u.query, keep_blank_values=True))
    path = re.sub(r"^/(live|movie|series|timeshift)/[^/]+/[^/]+/", rf"/\1/{MASK}/{MASK}/", u.path)
    return urlunsplit((u.scheme, netloc, path, q, u.fragment))


def scrub(text: str, secrets: list[str]) -> str:
    """*text* (an error message, which may quote the address) without any
    of *secrets* in it."""
    for s in sorted((s for s in secrets if s and len(s) >= 3), key=len, reverse=True):
        text = text.replace(s, MASK)
    return text


def source_secrets(source: dict | None) -> list[str]:
    s = source or {}
    return [str(s.get(k) or "") for k in ("username", "password", "mac")]


def _shown_headers(headers: dict[str, str]) -> list[str]:
    return [f"{k}: {MASK if any(w in k.lower() for w in _SECRET_HEADERS) else v}"
            for k, v in headers.items()]


# -- what to ask -------------------------------------------------------------

def probe_url(source: dict | None) -> str | None:
    """The address a check asks of the active source: one that answers fast
    and says whether the login holds."""
    s = source or {}
    kind = str(s.get("kind") or "xtream")
    server = str(s.get("server") or "").strip().rstrip("/")
    if not server or kind == "local":
        return None
    if "://" not in server:
        server = "http://" + server
    if kind == "xtream":
        return f"{server}/player_api.php?" + urlencode(
            {"username": s.get("username") or "", "password": s.get("password") or ""})
    return server + {"enigma2": "/web/about", "tvheadend": "/api/serverinfo",
                     "hdhomerun": "/discover.json"}.get(kind, "")


# -- the network -------------------------------------------------------------

def _elapsed_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def check_request(url: str, user_agent: str = "", headers: dict[str, str] | None = None,
                  proxy: str = "", timeout: float = 8.0,
                  secrets: list[str] | None = None, about_proxy: bool = False) -> Check:
    """Ask *url* the way the app would with these settings. *about_proxy*:
    the check is the proxy's (its own words when the proxy is what fails)."""
    import requests
    secrets = list(secrets or [])
    hdrs = dict(headers or {})
    if user_agent:
        hdrs["User-Agent"] = user_agent
    lines = [f"GET {redact_url(url)}"]
    if proxy:
        lines.append(f"Proxy: {redact_url(proxy)}")
    lines += _shown_headers(hdrs)
    session = requests.Session()
    if proxy:
        session.trust_env = False
        session.proxies.update({"http": proxy, "https": proxy})
    t0 = time.monotonic()
    try:
        r = session.get(url, headers=hdrs, timeout=timeout, stream=True, allow_redirects=True)
        try:
            r.raw.read(512, decode_content=False)
        except Exception:
            pass
        ms = _elapsed_ms(t0)
        code = r.status_code
        lines.append(f"Result: HTTP {code} {r.reason or ''} ({ms} ms)".rstrip())
        server = r.headers.get("Server")
        if server:
            lines.append(f"Server: {server}")
        r.close()
    except requests.exceptions.ProxyError as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_proxy_down", {}, scrub("\n".join(lines), secrets))
    except requests.exceptions.SSLError as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_tls", {}, scrub("\n".join(lines), secrets))
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_timeout", {"s": int(timeout)}, scrub("\n".join(lines), secrets))
    except requests.exceptions.RequestException as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_unreachable", {}, scrub("\n".join(lines), secrets))
    finally:
        session.close()
    detail = scrub("\n".join(lines), secrets)
    if code == 407:
        return Check(FAIL, "nerd_ck_proxy_auth", {"code": code}, detail)
    if code in (401, 403):
        return Check(FAIL, "nerd_ck_denied", {"code": code}, detail)
    if code >= 500:
        return Check(FAIL, "nerd_ck_server_error", {"code": code}, detail)
    if code >= 400:
        return Check(WARN, "nerd_ck_http_odd", {"code": code}, detail)
    return Check(OK, "nerd_ck_proxy_ok" if about_proxy else "nerd_ck_http_ok",
                 {"code": code, "ms": ms}, detail)


def check_proxy_listens(proxy: str, timeout: float = 4.0) -> Check:
    """With no source to ask through it: does anything answer at the proxy's
    address at all."""
    u = urlsplit(proxy if "://" in proxy else "http://" + proxy)
    host, port = u.hostname or "", u.port or (443 if u.scheme == "https" else 8080)
    lines = [f"TCP {host}:{port}"]
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_proxy_down", {}, "\n".join(lines))
    ms = _elapsed_ms(t0)
    lines.append(f"Result: connected ({ms} ms)")
    return Check(OK, "nerd_ck_proxy_listens", {"ms": ms}, "\n".join(lines))


def check_ip_family(url: str, family: str, timeout: float = 4.0) -> Check:
    """Reach *url*'s host over IPv4 or IPv6 ("auto": say which of them
    work)."""
    u = urlsplit(url if "://" in url else "http://" + url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)

    def one(fam: int) -> tuple[bool, str, str]:
        name = "IPv6" if fam == socket.AF_INET6 else "IPv4"
        try:
            infos = socket.getaddrinfo(host, port, fam, socket.SOCK_STREAM)
        except OSError as e:
            return False, "", f"{name}: no address ({type(e).__name__}: {e})"
        if not infos:
            return False, "", f"{name}: no address"
        addr = infos[0][4]
        t0 = time.monotonic()
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(addr)
        except OSError as e:
            return False, str(addr[0]), f"{name}: {addr[0]} port {port}: {type(e).__name__}: {e}"
        return True, str(addr[0]), f"{name}: {addr[0]} port {port}: connected ({_elapsed_ms(t0)} ms)"

    lines = [f"Host: {host}"]
    if family in ("ipv4", "ipv6"):
        fam = socket.AF_INET6 if family == "ipv6" else socket.AF_INET
        ok, addr, line = one(fam)
        lines.append(line)
        name = "IPv6" if family == "ipv6" else "IPv4"
        if ok:
            return Check(OK, "nerd_ck_ip_ok", {"ip": name, "addr": addr}, "\n".join(lines))
        key = "nerd_ck_ip_unreachable" if addr else "nerd_ck_ip_none"
        return Check(FAIL, key, {"ip": name}, "\n".join(lines))
    v4, _a4, l4 = one(socket.AF_INET)
    v6, _a6, l6 = one(socket.AF_INET6)
    lines += [l4, l6]
    return Check(OK if (v4 or v6) else FAIL, "nerd_ck_ip_auto",
                 {"v4": "✓" if v4 else "✗", "v6": "✓" if v6 else "✗"}, "\n".join(lines))


def check_udpxy(url: str, timeout: float = 5.0) -> Check:
    """udpxy's status page: there and answering."""
    import requests
    base = url.strip().rstrip("/")
    if "://" not in base:
        base = "http://" + base
    target = base + "/status"
    lines = [f"GET {redact_url(target)}"]
    t0 = time.monotonic()
    try:
        r = requests.get(target, timeout=timeout)
    except requests.exceptions.RequestException as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return Check(FAIL, "nerd_ck_udpxy_down", {}, "\n".join(lines))
    ms = _elapsed_ms(t0)
    lines.append(f"Result: HTTP {r.status_code} ({ms} ms)")
    if r.status_code != 200:
        return Check(FAIL, "nerd_ck_udpxy_odd", {"code": r.status_code}, "\n".join(lines))
    if "udpxy" not in r.text[:4000].lower():
        return Check(WARN, "nerd_ck_udpxy_not", {}, "\n".join(lines))
    return Check(OK, "nerd_ck_udpxy_ok", {"ms": ms}, "\n".join(lines))


# -- files -------------------------------------------------------------------

def check_picons(folder: str, rows: list[dict] | None = None) -> Check:
    from .nerd_lists import _picon_names, picon_index
    path = os.path.expanduser(folder.strip())
    if not os.path.isdir(path):
        return Check(FAIL, "nerd_ck_folder_missing", {}, f"Not a folder: {path}")
    index = picon_index(path)
    if not index:
        return Check(WARN, "nerd_ck_picons_none", {}, f"{path}: no .png/.svg/.jpg files")
    if not rows:
        return Check(OK, "nerd_ck_picons_count", {"n": len(index)}, f"{path}: {len(index)} picon(s)")
    channels = [r for r in rows if r.get("stream_id") is not None]
    matched = [r for r in channels if any(n.lower() in index for n in _picon_names(r))]
    missing = [str(r.get("name") or "") for r in channels if r not in matched][:40]
    detail = f"{path}: {len(index)} picon(s)\nMatched: {len(matched)} of {len(channels)} channels"
    if missing:
        detail += "\nNo picon for: " + ", ".join(missing)
    state = OK if matched else WARN
    return Check(state, "nerd_ck_picons", {"n": len(index), "matched": len(matched),
                                           "total": len(channels)}, detail)


def check_mpv_dir(folder: str) -> Check:
    path = os.path.expanduser(folder.strip())
    if not os.path.isdir(path):
        return Check(FAIL, "nerd_ck_folder_missing", {}, f"Not a folder: {path}")
    scripts_dir = os.path.join(path, "scripts")
    try:
        scripts = sorted(n for n in os.listdir(scripts_dir) if n.endswith((".lua", ".js")))
    except OSError:
        scripts = []
    conf = os.path.join(path, "mpv.conf")
    lines = [f"Folder: {path}"]
    try:
        with open(conf, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        lines.append("mpv.conf: none")
        lines.append(f"Scripts: {', '.join(scripts) or 'none'}")
        state = OK if scripts else WARN
        return Check(state, "nerd_ck_mpvconf_none", {"scripts": len(scripts)}, "\n".join(lines))
    pairs = nerd.parse_mpv_options(text)
    refused = _refused_in(text)
    lines.append(f"mpv.conf: {len(pairs)} option(s): {', '.join(k for k, _v in pairs) or '-'}")
    if refused:
        lines.append(f"Left out (they would take the picture out of the app): {', '.join(refused)}")
    lines.append(f"Scripts: {', '.join(scripts) or 'none'}")
    return Check(WARN if refused else OK, "nerd_ck_mpvconf",
                 {"n": len(pairs), "scripts": len(scripts)}, "\n".join(lines))


def _refused_in(text: str) -> list[str]:
    out = []
    for raw in (text or "").splitlines():
        key = raw.strip().lstrip("-").split("=", 1)[0].strip().lower().replace("_", "-")
        if key in nerd.REFUSED and key not in out:
            out.append(key)
    return out


def check_shaders(text: str) -> Check:
    paths = [p.strip() for p in re.split(r"[\n;]", text or "") if p.strip()]
    missing = [p for p in paths if not os.path.isfile(os.path.expanduser(p))]
    lines = [f"{'✗ missing' if p in missing else '✓'}  {p}" for p in paths]
    if missing:
        return Check(FAIL, "nerd_ck_shaders_missing", {"missing": len(missing), "total": len(paths)},
                     "\n".join(lines))
    return Check(OK, "nerd_ck_shaders_ok", {"n": len(paths)}, "\n".join(lines))


# -- mpv itself --------------------------------------------------------------

def check_mpv_options(text: str, extra: list[tuple[str, str]] | None = None,
                      make_mpv: Callable[[], Any] | None = None) -> Check:
    """Each typed option (and *extra*, the page's own) set on a real mpv
    core made for the purpose by *make_mpv* (media.players.throwaway_mpv;
    core never loads the player itself): which it takes, which it refuses
    and why."""
    pairs = nerd.parse_mpv_options(text) + list(extra or [])
    refused = _refused_in(text)
    if not pairs and not refused:
        return Check(INFO, "nerd_ck_mpv_empty")
    try:
        m = make_mpv() if make_mpv is not None else None
    except Exception as e:
        log.info("nerd check: no mpv core: %s", e)
        m = None
    if m is None:
        return Check(INFO, "nerd_ck_mpv_unavailable")
    bad: list[str] = []
    try:
        for key, value in pairs:
            try:
                m[key] = value
            except Exception as e:
                bad.append(f"{key}={value}  →  {_mpv_reason(e)}")
    finally:
        try:
            m.terminate()
        except Exception:
            pass
    lines = [f"{'✗' if any(b.startswith(k + '=') for b in bad) else '✓'}  {k}={v}" for k, v in pairs]
    for k in refused:
        lines.append(f"✗  {k}  →  not allowed: it would take the picture out of the app")
    if bad:
        lines.append("")
        lines += bad
    total = len(pairs) + len(refused)
    wrong = len(bad) + len(refused)
    if wrong:
        return Check(FAIL, "nerd_ck_mpv_refused", {"bad": wrong, "n": total}, "\n".join(lines))
    return Check(OK, "nerd_ck_mpv_ok", {"n": total}, "\n".join(lines))


def _mpv_reason(e: Exception) -> str:
    text = str(e) or type(e).__name__
    # python-mpv: ('mpv property does not exist', -8, (<handle>, b'key', ...)).
    m = re.search(r"\('([^']+)',\s*(-?\d+)", text)
    if m:
        code = int(m.group(2))
        meaning = {-5: "unknown option", -8: "unknown option",
                   -7: "value not accepted", -9: "value not accepted",
                   -6: "wrong kind of value", -10: "not settable now"}.get(code, "refused")
        return f"{meaning} (mpv: {m.group(1)}, error {code})"
    return text


# -- the VPN -----------------------------------------------------------------

def check_vpn(iface: str = "") -> Check:
    """Is a tunnel up now - the one named, or any VPN the app knows."""
    import sys
    at = time.strftime("%H:%M:%S")
    up = nerd.vpn_interfaces()
    lines = [f"Checked at {at}", f"Tunnels up: {', '.join(up) or 'none'}"]
    name = iface.strip()
    if name:
        lines.insert(1, f"Asked for: {name}")
        if sys.platform.startswith("linux"):
            found = nerd._is_up(name)
        else:
            found = any(name.lower() in t.lower() for t in nerd.active_tunnels())
        key = "nerd_ck_vpn_named" if found else "nerd_ck_vpn_named_down"
        return Check(OK if found else FAIL, key, {"name": name, "at": at}, "\n".join(lines))
    if up:
        return Check(OK, "nerd_ck_vpn_up", {"names": ", ".join(up), "at": at}, "\n".join(lines))
    return Check(WARN, "nerd_ck_vpn_none", {"at": at}, "\n".join(lines))
