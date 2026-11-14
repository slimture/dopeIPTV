"""Nerd: a source's own catch-up (archive) address, for the providers whose
archive the app's own guesses do not find.

A template is filled in for one programme with the placeholders Kodi's IPTV
Simple Client uses (a leading $ is allowed on each: ${start} = {start}):

    {utc} {start}                  the programme's start, Unix time
    {utcend} {end}                 its end
    {lutc} {now} {timestamp}       now
    {duration} {duration:60}       its length in seconds - or divided, here minutes
    {offset} {offset:60}           how long ago it started, the same way
    {Y} {m} {d} {H} {M} {S}        its start's year ... second, UTC
    {utc:Y-m-d:H-M}                a start, end or now written out, UTC
    {catchup-id}                   the channel's catch-up id, when it has one

and the M3U ``catchup`` modes: ``default`` (the template is the whole
address), ``append`` (it goes after the channel's live address), ``shift``
(?utc=...&lutc=... on the live address) and ``flussonic`` (the live address
rewritten: .../index-START-DURATION.m3u8, .../archive-START-DURATION.ts).
``auto`` reads the template: a whole address is ``default``, the rest
``append``.

Nothing here while Nerd is off, for a kind of source without an archive, or
with no template and the mode on auto: the app's own archive addresses are
used as before. A template's address is only ever tried first - when it
does not answer with a stream, the app's own come after it.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

from . import nerd
from .nerd_check import (
    FAIL, INFO, OK, WARN, Check, _shown_headers, redact_url, scrub,
    source_secrets,
)

MODES = ("auto", "default", "append", "shift", "flussonic")
# Kodi's other names for the same modes.
_ALIASES = {"": "auto", "fs": "flussonic", "flussonic-hls": "flussonic",
            "flussonic-ts": "flussonic"}
# The kinds of source whose channels can have an archive the app plays.
KINDS = ("xtream", "m3u")

_TOKEN = re.compile(r"\$?\{([A-Za-z][A-Za-z-]*)(?::([^{}]*))?\}")
_TIMES = {"utc": "start", "start": "start", "utcend": "end", "end": "end",
          "lutc": "now", "now": "now", "timestamp": "now"}
_SPANS = ("duration", "offset")
_PARTS = {"Y": "%Y", "m": "%m", "d": "%d", "H": "%H", "M": "%M", "S": "%S"}
_FORMAT_LETTERS = re.compile(r"[YmdHMS]")

#: What the source dialog's Details lists as known.
KNOWN = ("{utc} {utcend} {lutc} {start} {end} {now} {timestamp} {duration} {duration:60} "
         "{offset} {offset:60} {Y} {m} {d} {H} {M} {S} {utc:Y-m-d:H-M} {catchup-id}")

# Query keys whose values a playlist address keeps its login in.
_LOGIN_KEYS = ("username", "password", "user", "pass", "token", "key", "mac")


def normal_mode(mode: str | None) -> str:
    """One of MODES (a mode the app does not know is auto)."""
    m = str(mode or "").strip().lower()
    m = _ALIASES.get(m, m)
    return m if m in MODES else "auto"


def _known(name: str, arg: str | None) -> bool:
    if name in _TIMES:
        return arg is None or bool(_FORMAT_LETTERS.search(arg))
    if name in _SPANS:
        return arg is None or (arg.isdigit() and int(arg) > 0)
    return arg is None and (name in _PARTS or name == "catchup-id")


def unknown_placeholders(template: str) -> list[str]:
    """The placeholders in *template* the app cannot fill in, as written."""
    out: list[str] = []
    for m in _TOKEN.finditer(template or ""):
        if not _known(m.group(1), m.group(2)) and m.group(0) not in out:
            out.append(m.group(0))
    return out


def _written(when: datetime, fmt: str) -> str:
    return _FORMAT_LETTERS.sub(lambda m: when.strftime(_PARTS[m.group(0)]), fmt)


def render(template: str, start: datetime, end: datetime, now: datetime | None = None,
           catchup_id: str = "") -> str:
    """*template* filled in for a programme from *start* to *end* (aware
    datetimes; a naive one is local time). One the app does not know is
    left as written."""
    now = now or datetime.now(timezone.utc)
    when = {"start": start.astimezone(timezone.utc), "end": end.astimezone(timezone.utc),
            "now": now.astimezone(timezone.utc)}
    stamp = {k: int(v.timestamp()) for k, v in when.items()}
    spans = {"duration": max(0, stamp["end"] - stamp["start"]),
             "offset": max(0, stamp["now"] - stamp["start"])}

    def fill(m: re.Match[str]) -> str:
        name, arg = m.group(1), m.group(2)
        if not _known(name, arg):
            return m.group(0)
        if name in _TIMES:
            which = _TIMES[name]
            return str(stamp[which]) if arg is None else _written(when[which], arg)
        if name in _SPANS:
            return str(spans[name] // (int(arg) if arg else 1))
        if name in _PARTS:
            return when["start"].strftime(_PARTS[name])
        return catchup_id or ""
    return _TOKEN.sub(fill, template or "")


def _append(live_url: str, part: str) -> str:
    if part.startswith("?") and "?" in live_url:
        part = "&" + part[1:]
    elif part.startswith("&") and "?" not in live_url:
        part = "?" + part[1:]
    return live_url + part


def shift_template(live_url: str) -> str:
    return _append(live_url, "?utc={utc}&lutc={lutc}")


_FLUSSONIC = re.compile(r"^(?P<pre>.+/)(?P<name>[^/?#]+?)(?P<ext>\.m3u8|\.ts)?(?P<qs>\?[^#]*)?$")


def flussonic_template(live_url: str) -> str | None:
    """A Flussonic live address as its archive's: .../NAME.m3u8 ->
    .../NAME-{utc}-{duration}.m3u8, .../mpegts (or a .ts) ->
    .../archive-{utc}-{duration}.ts; the query (a token) stays. None for an
    address of another shape."""
    m = _FLUSSONIC.match(live_url or "")
    if not m or "://" not in m.group("pre") or "/" not in m.group("pre").split("://", 1)[1]:
        return None
    pre, name, ext, qs = m.group("pre"), m.group("name"), m.group("ext"), m.group("qs") or ""
    if ext == ".m3u8":
        return f"{pre}{name}-{{utc}}-{{duration}}.m3u8{qs}"
    if ext == ".ts" or (ext is None and name == "mpegts"):
        return f"{pre}archive-{{utc}}-{{duration}}.ts{qs}"
    return None


def template_for(live_url: str, template: str = "", mode: str = "auto",
                 channel_source: str = "") -> str | None:
    """The whole archive address for a channel, placeholders still in it -
    or None, when the source asks for nothing (the app's own then). The
    channel's own catchup-source stands in for an empty *template* in the
    modes that need one."""
    mode = normal_mode(mode)
    tpl = (template or "").strip()
    if mode == "auto":
        if not tpl:
            return None
        mode = "default" if "://" in tpl else "append"
    if mode == "shift":
        return shift_template(live_url) if live_url else None
    if mode == "flussonic":
        return flussonic_template(live_url)
    tpl = tpl or (channel_source or "").strip()
    if not tpl:
        return None
    if mode == "default":
        return tpl
    return _append(live_url, tpl) if live_url else None


def archive_url(live_url: str, start: datetime, end: datetime, template: str = "",
                mode: str = "auto", now: datetime | None = None, catchup_id: str = "",
                channel_source: str = "") -> str | None:
    """The address a programme from *start* to *end* plays from, or None:
    nothing asked, or no http(s) address comes of it."""
    tpl = template_for(live_url, template, mode, channel_source)
    if tpl is None:
        return None
    url = render(tpl, start, end, now, catchup_id)
    return url if url.lower().startswith(("http://", "https://")) else None


# -- the source --------------------------------------------------------------

def settings_of(source: dict | None) -> tuple[str, str]:
    """(template, mode) a source's options hold."""
    s = source or {}
    return str(s.get("catchup_template") or "").strip(), normal_mode(s.get("catchup_mode"))


def wanted(source: dict | None) -> bool:
    """Nerd is on and this source (core.nerd.source()'s form: its options
    and _kind) has a template or a mode of its own."""
    if not nerd.current().on or source is None:
        return False
    if str(source.get("_kind") or "xtream") not in KINDS:
        return False
    template, mode = settings_of(source)
    return bool(template) or mode != "auto"


def for_programme(source: dict | None, live_url: str, start: datetime, end: datetime,
                  catchup_id: str = "", channel_source: str = "",
                  now: datetime | None = None) -> str | None:
    """What the window tries first for an archive programme: the source's
    own address, or None (as without Nerd)."""
    if not wanted(source) or not live_url:
        return None
    template, mode = settings_of(source)
    return archive_url(live_url, start, end, template, mode, now, catchup_id, channel_source)


def secrets_of(source: dict | None) -> list[str]:
    """Logins a source's addresses carry: its fields, and the ones in a
    playlist address's query (get.php?username=...&password=...)."""
    s = source or {}
    out = source_secrets(s)
    try:
        q = parse_qsl(urlsplit(str(s.get("server") or "")).query)
    except ValueError:
        q = []
    out += [v for k, v in q if k.lower() in _LOGIN_KEYS]
    return [v for v in out if v]


def shown(url: str, secrets: list[str] | None = None) -> str:
    """*url* without its logins, for a line on screen."""
    return scrub(redact_url(url), list(secrets or []))


def _wrappable(text: str) -> str:
    # A zero-width space after each / ? & (not inside the scheme's //) lets
    # a long address wrap in a narrow line instead of widening the dialog.
    return re.sub(r"(?<![:/])([/?&])(?!/)", "\\1\u200b", text)


# -- checks (the line under the field) -----------------------------------------

def sample_programme(now: datetime) -> tuple[datetime, datetime]:
    """The programme a check plays: an hour that began two hours ago."""
    start = (now - timedelta(hours=2)).replace(second=0, microsecond=0)
    return start, start + timedelta(hours=1)


def check_template(template: str, mode: str, live_url: str, channel: str = "",
                   example: bool = False, catchup_id: str = "", channel_source: str = "",
                   secrets: list[str] | None = None, now: datetime | None = None) -> Check:
    """What a programme two hours ago would play from with this template and
    mode on a channel whose live address is *live_url* (*example*: a made-up
    one) - or which placeholder is unknown, or why nothing comes of it."""
    tpl = (template or "").strip()
    m = normal_mode(mode)
    if not tpl and m == "auto":
        return Check(INFO, "nerd_ck_cu_app")
    now = now or datetime.now(timezone.utc)
    start, end = sample_programme(now)
    s_utc, e_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    lines = [f"Mode: {m}", f"Template: {tpl or '-'}",
             f"Channel: {channel or '-'}{' (example)' if example else ''}",
             f"Live address: {shown(live_url, secrets)}",
             f"Programme: {s_utc:%Y-%m-%d %H:%M} - {e_utc:%H:%M} UTC"]
    bad = unknown_placeholders(tpl) if m not in ("shift", "flussonic") else []
    if bad:
        lines.append("Known: " + KNOWN)
        return Check(FAIL, "nerd_ck_tpl_unknown", {"names": ", ".join(bad)}, "\n".join(lines))
    url = archive_url(live_url, start, end, tpl, m, now, catchup_id, channel_source)
    if url is None:
        if m == "flussonic":
            return Check(WARN, "nerd_ck_cu_not_flussonic", {}, "\n".join(lines))
        if m in ("default", "append") and not (tpl or channel_source):
            return Check(WARN, "nerd_ck_cu_need_template", {}, "\n".join(lines))
        lines.append(f"Address: {shown(template_for(live_url, tpl, m, channel_source) or '', secrets)}")
        return Check(FAIL, "nerd_ck_cu_not_address", {}, "\n".join(lines))
    addr = shown(url, secrets)
    lines += [f"Address: {addr}", "Known: " + KNOWN]
    if example:
        return Check(OK, "nerd_ck_cu_would_example", {"url": _wrappable(addr)}, "\n".join(lines))
    return Check(OK, "nerd_ck_cu_would", {"channel": channel, "url": _wrappable(addr)},
                 "\n".join(lines))


def is_stream(content_type: str, first: bytes) -> bool | None:
    """True: what came back is video (an MPEG-TS packet, an HLS playlist, a
    video type); False: a page (HTML, JSON); None: neither - the same rule
    the window's archive probe goes by."""
    ctype = (content_type or "").lower()
    head = (first or b"").lstrip()
    if "text/html" in ctype or head[:1] in (b"<", b"{"):
        return False
    if (first[:1] == b"\x47" or head[:7] == b"#EXTM3U" or "mpegurl" in ctype
            or "video" in ctype or "octet-stream" in ctype):
        return True
    return None


def check_archive(url: str, user_agent: str = "", headers: dict[str, str] | None = None,
                  proxy: str = "", timeout: float = 15.0,
                  secrets: list[str] | None = None, channel: str = "") -> Check:
    """Ask the archive address as the app's probe does (the first bytes,
    with the source's user agent, headers and proxy) and say what came
    back. *channel*: whose archive it is, for the line."""
    import requests
    secrets = list(secrets or [])
    hdrs = dict(headers or {})
    if user_agent:
        hdrs["User-Agent"] = user_agent
    hdrs["Range"] = "bytes=0-8191"
    lines = [f"Channel: {channel}"] if channel else []
    lines.append(f"GET {shown(url, secrets)}")
    if proxy:
        lines.append(f"Proxy: {redact_url(proxy)}")
    lines += _shown_headers(hdrs)
    session = requests.Session()
    if proxy:
        session.trust_env = False
        session.proxies.update({"http": proxy, "https": proxy})
    t0 = time.monotonic()
    first = b""
    ctype = ""
    try:
        r = session.get(url, headers=hdrs, timeout=timeout, stream=True, allow_redirects=True)
        try:
            first = next(r.iter_content(4096), b"") or b""
        except Exception:
            pass
        ms = int((time.monotonic() - t0) * 1000)
        code = r.status_code
        ctype = r.headers.get("Content-Type") or ""
        lines.append(f"Result: HTTP {code} {r.reason or ''} ({ms} ms)".rstrip())
        if ctype:
            lines.append(f"Content-Type: {ctype}")
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
    if code in (404, 410):
        return Check(FAIL, "nerd_ck_cu_missing", {"code": code}, detail)
    if code >= 500:
        return Check(FAIL, "nerd_ck_server_error", {"code": code}, detail)
    if code >= 400:
        return Check(WARN, "nerd_ck_http_odd", {"code": code}, detail)
    name = channel or "?"
    if is_stream(ctype, first):
        return Check(OK, "nerd_ck_cu_ok", {"channel": name, "code": code, "ms": ms}, detail)
    return Check(WARN, "nerd_ck_cu_not_stream", {"channel": name, "code": code}, detail)
