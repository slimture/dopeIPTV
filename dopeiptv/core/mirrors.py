"""Nerd: a source's backup addresses - other addresses of the same account.

Providers often hand out several addresses for one account (a main domain
and a "backup DNS" or two). In a source's Nerd options they are a list, one
per line. While Nerd is on, the client tries the next one when the address
in use cannot be reached - the name does not resolve, nothing answers or it
refuses, TLS fails, or the server answers 5xx (Cloudflare's 520-527 among
them) - and keeps the one that answered for this source
(``nerd_mirror_<source id>``), building every address it hands out from it.
A login that is refused (401, 403) or a path that is not there (404) says
nothing about the server being down, and never switches.

What uses them: Xtream - the API, the live, film, episode and catch-up
addresses and the guide (xmltv.php); M3U - the playlist's own address (the
channels' addresses are what the playlist says); Stalker portals - the
portal's API (its play links come from the portal itself).

With Nerd off, or a source without backup addresses, nothing here changes
anything: the address typed in the source is the only one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlencode, urlsplit

from .log import log
from .nerd_check import FAIL, INFO, OK, WARN, Check, scrub

KEY = "backup_servers"
#: The kinds whose clients take backup addresses.
KINDS = ("xtream", "m3u", "stalker")
MAX_ADDRESSES = 10
PICK_PREFIX = "nerd_mirror_"
#: Why a line is refused (the check line's words for each, ``nerd_ck_mr_<why>``).
REASONS = ("space", "scheme", "host", "port", "login", "query", "many")


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


@dataclass(frozen=True)
class Problem:
    line: int           # 1-based, as the box numbers its lines
    text: str           # the line as typed
    reason: str         # one of REASONS


# -- the list ------------------------------------------------------------------

def lines_of(value: Any) -> list[tuple[int, str]]:
    """(line number, text) of each line that says something: blank lines
    and # comments are no address."""
    if isinstance(value, (list, tuple)):
        raw = [str(v) for v in value]
    else:
        raw = str(value or "").splitlines()
    out = []
    for i, line in enumerate(raw, 1):
        s = line.strip()
        if s and not s.startswith("#"):
            out.append((i, s))
    return out


def normalize(address: str) -> str:
    """*address* as a client keeps its server: a scheme (http:// when none
    was typed) and no slash at the end."""
    s = (address or "").strip()
    if s and "://" not in s:
        s = "http://" + s
    return s.rstrip("/")


def problem(line: str, kind: str = "xtream") -> str:
    """Why *line* cannot be a backup address of a source of *kind* (one of
    REASONS), or "" when it can."""
    s = (line or "").strip()
    if any(c.isspace() for c in s):
        return "space"
    if "://" in s and s.split("://", 1)[0].lower() not in ("http", "https"):
        return "scheme"
    try:
        u = urlsplit(normalize(s))
    except ValueError:
        return "host"
    if "@" in u.netloc:
        return "login"
    host = u.hostname or ""
    if not host or not _host_ok(host):
        return "host"
    try:
        port = u.port
    except ValueError:
        return "port"
    if port is not None and not 1 <= port <= 65535:
        return "port"
    if kind != "m3u" and (u.query or u.fragment):
        return "query"
    return ""


def _host_ok(host: str) -> bool:
    if ":" in host:                     # an IPv6 address, from [...]
        return all(c in "0123456789abcdef:." for c in host.lower())
    return all(c.isalnum() or c in "-._" for c in host) and not host.startswith(("-", "."))


def problems(value: Any, kind: str = "xtream") -> list[Problem]:
    """Each line that cannot be an address, and why; a line past
    MAX_ADDRESSES is one too many."""
    out = []
    good = 0
    for n, text in lines_of(value):
        why = problem(text, kind)
        if not why:
            good += 1
            if good > MAX_ADDRESSES:
                why = "many"
        if why:
            out.append(Problem(n, text, why))
    return out


def parse(value: Any, kind: str = "xtream") -> list[str]:
    """The addresses that can be used, normalized, each once, in order."""
    out: list[str] = []
    for _n, text in lines_of(value):
        if problem(text, kind):
            continue
        addr = normalize(text)
        if not any(same(addr, a) for a in out):
            out.append(addr)
        if len(out) >= MAX_ADDRESSES:
            break
    return out


def same(a: str, b: str) -> bool:
    """One address however it was written (case of the scheme and host, a
    default port, a slash at the end)."""
    return _key(a) == _key(b)


def _key(address: str) -> tuple[str, str, int, str]:
    try:
        u = urlsplit(normalize(address))
        port = u.port or (443 if u.scheme.lower() == "https" else 80)
    except ValueError:
        return ("", address, 0, "")
    rest = u.path + ("?" + u.query if u.query else "")
    return (u.scheme.lower(), (u.hostname or "").lower(), port, rest)


def addresses(primary: str, options: dict | None, kind: str = "xtream") -> list[str]:
    """The source's own address and its backup addresses after it - only
    while Nerd is on, for the kinds that take them. [primary] otherwise."""
    first = normalize(primary)
    from . import nerd
    if kind not in KINDS or not (options or {}).get(KEY) or not nerd.current().on:
        return [first]
    return [first] + [a for a in parse((options or {}).get(KEY), kind) if not same(a, first)]


def shown(address: str) -> str:
    """*address* as a log line or the check line shows it: its scheme, host
    and port - never a path or query (an M3U address may carry a login)."""
    try:
        u = urlsplit(normalize(address))
        host = u.hostname or ""
        port = u.port
    except ValueError:
        return "?"
    if ":" in host:
        host = f"[{host}]"
    return f"{u.scheme}://{host}" + (f":{port}" if port else "")


# -- when to try the next one ------------------------------------------------------

def fails_over_status(code: int | None) -> bool:
    """An answer that says the server is down: 5xx, Cloudflare's 52x among
    them. 401/403 (the login) and 404 (the path) never do."""
    return code is not None and 500 <= int(code) <= 599


#: Exceptions (by class name, so core needs no requests) whose meaning is
#: "this server cannot be reached": no such name, refused, no answer in
#: time while connecting, TLS failed. A proxy that is down is not the
#: server's fault, and a read that timed out came from a server that is up.
_DOWN = {"ConnectionError", "ConnectTimeout", "SSLError", "NameResolutionError",
         "ConnectionRefusedError", "gaierror", "SSLCertVerificationError"}
_NOT = {"ProxyError", "ReadTimeout", "ChunkedEncodingError"}


def fails_over_error(e: BaseException) -> bool:
    names = {c.__name__ for c in type(e).__mro__}
    return bool(names & _DOWN) and not names & _NOT


def tail(url: str, base: str) -> str | None:
    """What *url* has after *base* (a path, from "/"), or None when it was
    not made from *base*."""
    b = normalize(base)
    if url == b:
        return ""
    if url.startswith(b) and url[len(b)] in "/?":
        return url[len(b):]
    return None


# -- the one in use ------------------------------------------------------------------

def pick_key(source_id: str) -> str:
    return PICK_PREFIX + str(source_id)


def remembered(settings: Settings | None, source_id: str | None) -> str:
    if settings is None or not source_id:
        return ""
    try:
        return str(settings.value(pick_key(source_id), "") or "")
    except Exception:
        return ""


def remember(settings: Settings | None, source_id: str | None, address: str) -> None:
    if settings is None or not source_id:
        return
    try:
        settings.setValue(pick_key(source_id), address)
    except Exception as e:
        log.info("backup addresses: not remembered: %s", e)


class Picker:
    """The addresses of one source and the one in use, in the order a
    request tries them: the one in use first, then the rest as listed.
    Thread-safe: the lists, the guide and the player ask from different
    threads. *on_switch* is told the address that answered when it is not
    the one in use before (the window remembers it for the source)."""

    def __init__(self, addresses: list[str], current: str = "",
                 on_switch: Callable[[str], None] | None = None, name: str = "") -> None:
        self._all = [normalize(a) for a in addresses if a] or [""]
        found = next((a for a in self._all if current and same(a, current)), None)
        self._current = found or self._all[0]
        self._on_switch = on_switch
        self._name = name or "source"
        self._lock = threading.Lock()

    @property
    def addresses(self) -> list[str]:
        return list(self._all)

    @property
    def primary(self) -> str:
        return self._all[0]

    @property
    def current(self) -> str:
        with self._lock:
            return self._current

    def order(self) -> list[str]:
        with self._lock:
            cur = self._current
        return [cur] + [a for a in self._all if a != cur]

    def worked(self, address: str) -> bool:
        """*address* answered: the one in use from now on. True when that is
        a change (logged, and *on_switch* told)."""
        with self._lock:
            match = next((a for a in self._all if same(a, address)), None)
            if match is None or match == self._current:
                return False
            before, self._current = self._current, match
        log.warning("%s: %s could not be reached - %s is used from now on",
                    self._name, shown(before), shown(match))
        if self._on_switch is not None:
            try:
                self._on_switch(match)
            except Exception as e:
                log.info("backup addresses: %s", e)
        return True


# -- the line under the list ----------------------------------------------------------

def check_list(value: Any, kind: str, primary: str, in_use: str = "") -> Check:
    """What the box says, without asking the network: the first line that
    cannot be an address and why, else which address is in use and how
    many there are to fall back on."""
    if kind not in KINDS:
        return Check(INFO, "nerd_ck_mr_kind")
    bad = problems(value, kind)
    if bad:
        p = bad[0]
        detail = "\n".join(f"Line {b.line}: {b.reason}" for b in bad)
        return Check(FAIL, "nerd_ck_mr_" + p.reason,
                     {"n": p.line, "line": p.text[:80], "max": MAX_ADDRESSES}, detail)
    found = parse(value, kind)
    first = normalize(primary)
    backups = [a for a in found if not same(a, first)]
    if not backups:
        return Check(INFO, "nerd_ck_mr_none")
    allof = [first] + backups
    use = next((a for a in allof if in_use and same(a, in_use)), first)
    lines = [f"{'→' if a == use else ' '} {shown(a)}{'  (the source)' if a == first else ''}"
             for a in allof]
    return Check(OK, "nerd_ck_mr_list", {"host": shown(use), "n": len(backups)}, "\n".join(lines))


def probe(address: str, kind: str, source: dict | None = None, user_agent: str = "",
          headers: dict[str, str] | None = None, proxy: str = "",
          timeout: float = 6.0) -> tuple[bool, int | None, str]:
    """(worker thread) Ask *address* what the client would ask first: an
    Xtream server its player_api.php with the source's login, a playlist its
    first bytes, a portal its own page. (answers, HTTP status or None, a
    line for the details)."""
    from ._lazy_requests import requests
    s = source or {}
    if kind == "xtream":
        url = f"{normalize(address)}/player_api.php?" + urlencode(
            {"username": s.get("username") or "", "password": s.get("password") or ""})
    else:
        url = normalize(address) + ("/" if kind == "stalker" else "")
    hdrs = dict(headers or {})
    if user_agent:
        hdrs["User-Agent"] = user_agent
    session = requests.Session()
    if proxy:
        session.trust_env = False
        session.proxies.update({"http": proxy, "https": proxy})
    t0 = time.monotonic()
    try:
        r = session.get(url, headers=hdrs, timeout=(timeout, timeout), stream=True,
                        allow_redirects=True)
        code = r.status_code
        r.close()
    except requests.RequestException as e:
        return False, None, f"{type(e).__name__}: {e}"
    finally:
        session.close()
    ms = int((time.monotonic() - t0) * 1000)
    # Up is any answer that is not the server saying it is down; a refused
    # login still came from a server that is there.
    return not fails_over_status(code), code, f"HTTP {code} ({ms} ms)"


def check_each(addrs: list[str], kind: str, source: dict | None = None,
               in_use: str = "", user_agent: str = "",
               headers: dict[str, str] | None = None, proxy: str = "",
               secrets: list[str] | None = None,
               ask: Callable[..., tuple[bool, int | None, str]] | None = None) -> Check:
    """(worker thread) Test: each address asked in turn - which answer, and
    which one is in use."""
    ask = ask or probe
    secrets = list(secrets or [])
    if len(addrs) < 2:
        return Check(INFO, "nerd_ck_mr_none")
    use = next((a for a in addrs if in_use and same(a, in_use)), addrs[0])
    lines, up = [], 0
    for a in addrs:
        ok, _code, said = ask(a, kind, source, user_agent, headers, proxy)
        up += 1 if ok else 0
        mark = "✓" if ok else "✗"
        lines.append(f"{mark} {shown(a)}{'  (in use)' if a == use else ''}  -  {said}")
    detail = scrub("\n".join(lines), secrets)
    params = {"up": up, "n": len(addrs), "host": shown(use)}
    if up == len(addrs):
        return Check(OK, "nerd_ck_mr_all", params, detail)
    if up == 0:
        return Check(FAIL, "nerd_ck_mr_down", params, detail)
    return Check(WARN, "nerd_ck_mr_some", params, detail)
