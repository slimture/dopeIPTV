"""Nothing that goes to dopeiptv.org carries a login of the person's.

Every request the app makes goes out through core/_lazy_requests.py's
adapter (rule 5 of AGENTS.md: no way around it), and that adapter asks
``refusal`` of each one before a byte is sent. A request to dopeiptv.org -
the update check, a report and its answers, a synced copy, a room's
messages, a release's file - that holds a login of any source the person
has, readable to the site, is not sent: in its address, its headers or its
body, as written or in an encoded form (percent-encoded, JSON-escaped, a
repr, HTTP Basic's base64 of a user name and its password). The caller
gets ``LoginRefused`` - a failed request - and the log says that one was
held back, never the value.

What a login is (``logins_of``): a source's user name, password, token and
box MAC; the login before an address's host and the values of its query's
login words (player_exec.LOGIN_WORDS) in its server, list, guide, proxy and
backup addresses; the value of a header of its own that opens something
(net_bridge.SAFE_HEADERS are those that do not); its whole addresses. They
are learnt as the sources are read and saved (``learn``: core/stores'
PlaylistStore) and read again from the settings before every check, so a
source added or synced since is known too (``_fresh``) - and with them the
person's other logins, every profile's (``account_logins``: Trakt's and
Simkl's tokens, OpenSubtitles', the sync code, Nerd's proxy and headers, a
Kodi's password). The codes that open a synced copy and a room - never
sent at all - are added by their own modules (``never``).

What is sealed is not read: a synced copy and a room's message are
ChaCha20-Poly1305 under a key from a code the site never has
(core/sync.py, core/together.py), and ciphertext holds a short word by
chance. Those modules hand their sealed bytes to ``sealed`` just before the
request; the check leaves exactly those bytes out and reads all the rest.

How a login is matched: one of ``LONG`` characters or more anywhere; a
shorter one only as a word of its own (not between letters or digits), and
only where the person's text can be - a header's value, a query's value,
a JSON document's values (never its keys) or a body that is no JSON - so a
short login does not match the app's own words. The app's own headers
(the user agent, the build's kind, the content type) are its own words.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import weakref
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qsl, unquote, unquote_plus, urlsplit

from .log import basic_forms, encoded_forms, log, register_secrets

#: dopeiptv.org and its names. Tests add this computer (``HOSTS``).
SERVICE_HOST = "dopeiptv.org"
HOSTS: set[str] = {SERVICE_HOST}
#: A login this long or longer is looked for anywhere; a shorter one only
#: as a word of its own.
LONG = 6
#: A whole address shorter than this is no secret worth the name.
ADDRESS_LEAST = 12
#: Headers whose values are the app's own words, never the person's.
OWN_HEADERS = frozenset((
    "user-agent", "content-type", "content-length", "accept", "accept-encoding",
    "connection", "x-dopeiptv-build", "x-dopeiptv-report"))
#: Sealed pieces kept for a request at most this long, this many at once.
SEALED_FOR_S = 120.0
SEALED_MAX = 32

_HEADER_LINE = re.compile(r"^\s*([A-Za-z0-9!#$%&'*+.^_`|~-]+)\s*[:=]\s*(.*?)\s*$")
_FIELDS = ("username", "password", "token", "mac")
_ADDRESSES = ("server", "url", "epg_url")
_ADDRESS_OPTIONS = ("proxy", "backup_servers")

_lock = threading.Lock()
_learnt: frozenset[str] = frozenset()
_never: set[str] = set()
_long: frozenset[str] = frozenset()
_short_re: re.Pattern[str] | None = None
_settings: Any = None
_seen_raw: str | None = None
_sealed: list[tuple[float, bytes]] = []


class LoginRefused(Exception):
    """A request to dopeiptv.org that held a login: not sent. Raised to the
    caller as requests' RequestException (core/_lazy_requests.py)."""


def _address_logins(text: str) -> set[str]:
    from .player_exec import LOGIN_WORDS
    out: set[str] = set()
    for line in str(text or "").splitlines():
        line = line.strip()
        if "://" not in line:
            continue
        if len(line) >= ADDRESS_LEAST:
            out.add(line)
        try:
            parts = urlsplit(line)
        except ValueError:
            continue
        info = parts.netloc.rpartition("@")[0]
        if info:
            user, _, password = info.partition(":")
            out |= {unquote(user), unquote(password)}
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if unquote_plus(key).strip().lower() in LOGIN_WORDS:
                out.add(value)
    return out


def _header_logins(value: Any) -> set[str]:
    from .net_bridge import SAFE_HEADERS
    if isinstance(value, Mapping):
        pairs = [(str(k), str(v)) for k, v in value.items()]
    else:
        pairs = [(m.group(1), m.group(2)) for ln in str(value or "").splitlines()
                 if (m := _HEADER_LINE.match(ln))]
    out: set[str] = set()
    for name, val in pairs:
        if name.strip().lower() in SAFE_HEADERS | {"user-agent"} or not val:
            continue
        out.add(val)
        scheme, _, rest = val.partition(" ")
        if rest and scheme.isalpha():
            out.add(rest.strip())       # "Basic xxx", "Bearer xxx": the value alone too
    return out


def logins_of(playlists: Iterable[Any]) -> set[str]:
    """Every login the sources *playlists* hold (see the module's words)."""
    out: set[str] = set()
    for p in playlists or []:
        if not isinstance(p, Mapping):
            continue
        for key in _FIELDS:
            v = str(p.get(key) or "").strip()
            if v:
                out.add(v)
                if key == "mac":
                    out |= {v.lower(), v.upper(), v.replace(":", ""), v.lower().replace(":", "")}
        user, password = str(p.get("username") or ""), str(p.get("password") or "")
        if user and password:
            out |= basic_forms(user, password)
        for key in _ADDRESSES:
            out |= _address_logins(str(p.get(key) or ""))
        opts = p.get("options")
        if isinstance(opts, Mapping):
            for key in _ADDRESS_OPTIONS:
                out |= _address_logins(str(opts.get(key) or ""))
            if opts.get("headers"):
                out |= _header_logins(opts.get("headers"))
    return {v for v in out if v and v.strip()}


def _rebuild() -> None:
    global _long, _short_re
    values: set[str] = set()
    for v in _learnt | _never:
        values.add(v)
        values |= encoded_forms(v)
    _long = frozenset(v for v in values if len(v) >= LONG)
    short = sorted((v for v in values if len(v) < LONG), key=len, reverse=True)
    _short_re = (re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, short))
                            + r")(?![A-Za-z0-9])") if short else None)


def learn(playlists: Iterable[Any], settings: Any = None) -> None:
    """The sources' logins known from now on - to this check and to the
    log's mask (core/log.register_secrets) before any client is made.
    *settings*: where the sources are kept, read again before a check."""
    global _learnt, _settings, _seen_raw
    rows = [p for p in playlists or [] if isinstance(p, Mapping)]
    found = logins_of(rows)
    for p in rows:
        # A user name with its password: the log masks the pair's HTTP
        # Basic too. Every other login alone - never a whole address,
        # which the log masks as an address already.
        register_secrets(str(p.get("username") or ""), str(p.get("password") or ""))
    for v in sorted(found):
        if "://" not in v:
            register_secrets(v)
    with _lock:
        if settings is not None:
            try:
                _settings = weakref.ref(settings)
            except TypeError:
                _settings = lambda s=settings: s      # noqa: E731
            _seen_raw = None
        if found - _learnt:
            _learnt = _learnt | found
            _rebuild()


def never(*values: str | None) -> None:
    """Values that never go to dopeiptv.org readable at all: the code that
    opens a synced copy, a room's code."""
    given = {str(v) for v in values if v and str(v).strip()}
    with _lock:
        if given - _never:
            _never.update(given)
            _rebuild()


def account_logins(settings: Any) -> set[str]:
    """The person's other logins the settings hold - every profile's: the
    watch-history and subtitle accounts' tokens and passwords, the sync
    code, Home Assistant's and a webhook's, Nerd's proxy and headers, a
    Kodi's password (core/backup.py's LOGIN_SETTINGS and the settings that
    hold one only sometimes)."""
    from .backup import _LOGIN_IN, LOGIN_SETTINGS
    plain = set(LOGIN_SETTINGS) - {"simkl_token_expires"}
    raw = settings.raw() if hasattr(settings, "raw") else settings
    try:
        keys = [str(k) for k in raw.allKeys()]
    except Exception:
        keys = []
    out: set[str] = set()
    for key in keys:
        name = key.rsplit("/", 1)[-1]
        if name not in plain and name not in _LOGIN_IN:
            continue
        try:
            value = raw.value(key, "")
        except Exception:
            continue
        if name == "nerd_proxy":
            out |= _address_logins(str(value or ""))
        elif name == "nerd_headers":
            out |= _header_logins(value)
        elif name == "kodi_devices":
            try:
                devices = json.loads(value) if isinstance(value, str) else value
            except ValueError:
                devices = []
            for d in devices if isinstance(devices, list) else []:
                if isinstance(d, Mapping) and d.get("password"):
                    out.add(str(d.get("password")))
        elif value and str(value).strip():
            out.add(str(value).strip())
    return out


def _fresh() -> None:
    """The sources and accounts as the settings hold them now: one added,
    signed in or synced since the last ``learn`` is known before anything
    is sent."""
    global _seen_raw, _learnt
    ref = _settings
    s = ref() if ref is not None else None
    if s is None:
        return
    try:
        raw = str(s.value("playlists", "") or "")
        accounts = account_logins(s)
    except Exception:
        return
    stamp = raw + "\0" + "\0".join(sorted(accounts))
    if stamp == _seen_raw:
        return
    try:
        rows = json.loads(raw) if raw else []
    except ValueError:
        rows = []
    learn(rows if isinstance(rows, list) else [])
    with _lock:
        if accounts - _learnt:
            _learnt = _learnt | accounts
            _rebuild()
    _seen_raw = stamp


def sealed(data: bytes | str) -> bytes | str:
    """*data* - bytes sealed under a key dopeiptv.org never has, or their
    base64 - left out of the next request's check that carries them.
    Returns *data*. Only core/sync.py and core/together.py call it, on
    what their seal made (tests/test_site_out.py)."""
    raw = data.encode("ascii") if isinstance(data, str) else bytes(data)
    now = time.monotonic()
    with _lock:
        _sealed[:] = [(t, b) for t, b in _sealed if now - t < SEALED_FOR_S][-(SEALED_MAX - 1):]
        _sealed.append((now, raw))
    return data


def _without_sealed(body: bytes) -> bytes:
    now = time.monotonic()
    with _lock:
        pieces = [b for t, b in _sealed if now - t < SEALED_FOR_S]
    for piece in sorted(pieces, key=len, reverse=True):
        if piece and piece in body:
            body = body.replace(piece, b"")
    return body


def _json_values(doc: Any, out: list[str], depth: int = 0) -> None:
    if depth > 32:
        out.append(json.dumps(doc))
    elif isinstance(doc, Mapping):
        for v in doc.values():
            _json_values(v, out, depth + 1)
    elif isinstance(doc, list):
        for v in doc:
            _json_values(v, out, depth + 1)
    elif doc is not None:
        out.append(str(doc))


def _service(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return True             # an address that does not parse: looked at, not let by
    return host in HOSTS or any(host.endswith("." + h) for h in HOSTS)


def refusal(url: str, headers: Mapping[str, Any] | None = None,
            body: bytes | str | None = None) -> str | None:
    """Why the request to *url* may not be sent - the part of it that holds
    a login (never the login) - or None. Anything not for dopeiptv.org: None."""
    if not _service(url):
        return None
    _fresh()
    with _lock:
        long_, short_re = _long, _short_re
    if not long_ and short_re is None:
        return None
    anywhere: list[tuple[str, str]] = [("its address", unquote(url)), ("its address", url)]
    worded: list[tuple[str, str]] = []
    try:
        query = urlsplit(url).query
    except ValueError:
        query = ""
    worded += [("its address", v) for _k, v in parse_qsl(query, keep_blank_values=True)]
    for name, value in (headers or {}).items():
        if str(name).strip().lower() in OWN_HEADERS:
            continue
        text = value.decode("latin-1") if isinstance(value, bytes) else str(value)
        anywhere.append(("a header", f"{name}: {text}"))
        worded.append(("a header", text))
    if body is not None and not isinstance(body, (bytes, bytearray, str)):
        return "its body (a stream, not read)"     # none of the app's: not let by unread
    if body:
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        raw = _without_sealed(raw)
        text = raw.decode("utf-8", "replace")
        anywhere.append(("its body", text))
        try:
            doc = json.loads(text)
        except (ValueError, RecursionError):
            worded.append(("its body", text))
        else:
            values: list[str] = []
            _json_values(doc, values)
            worded += [("its body", v) for v in values]
    for where, text in anywhere:
        if any(v in text for v in long_):
            return where
    if short_re is not None:
        for where, text in worded:
            if short_re.search(text):
                return where
    return None


_told: set[str] = set()


def check(url: str, headers: Mapping[str, Any] | None = None,
          body: bytes | str | None = None) -> None:
    """``refusal`` as an error: LoginRefused when the request may not go,
    said in the log once per place - never with the value."""
    why = refusal(url, headers, body)
    if why is None:
        return
    key = hashlib.sha256(f"{urlsplit(url).path}|{why}".encode()).hexdigest()
    if key not in _told:
        _told.add(key)
        log.warning("dopeiptv.org: a request held a login of a source in %s - nothing sent", why)
    raise LoginRefused(f"it held a login of a source in {why} - not sent")


def forget_for_tests() -> None:
    """Tests: nothing learnt."""
    global _learnt, _never, _settings, _seen_raw
    with _lock:
        _learnt, _settings, _seen_raw = frozenset(), None, None
        _never = set()
        _sealed.clear()
        _told.clear()
        _rebuild()
