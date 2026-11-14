"""Boxes and servers on the home network, behind XtreamClient's interface.

What the Enigma2, Tvheadend and HDHomeRun clients share: the address and an
optional login (basic or digest, whichever the box asks for), a catalogue
loaded once and kept when a refresh fails, stable channel ids (a favourite
survives a box that reorders its bouquets), the box's recordings as films,
and the box's own programme guide handed to the guide as XMLTV
(``own_guide``), so the guide grid, search and reminders work unchanged.
"""

from __future__ import annotations

import re
import threading
import time
import zlib
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from ..core._lazy_requests import requests
from ..core.log import log, register_secrets
from ..i18n import LocalizedError, tr
from .client import OfflineClient

RECORDINGS = "box-recordings"
USER_AGENT = "dopeIPTV/1.0"

# Characters XML 1.0 forbids, which a box copies from the broadcast now and
# then (and which stop the whole parse), and the C1 controls DVB uses to
# mark emphasis in names ("\x86News\x87").
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_C1 = re.compile(r"[\x80-\x9f]")
_DECL = re.compile(r"^\s*<\?xml[^>]*\?>", re.S)
_DOCTYPE = re.compile(r"<!DOCTYPE[^>\[]*(\[[^\]]*\])?\s*>", re.S | re.I)
_ENCODING = re.compile(rb"^\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)")


def clean_text(value: Any, lines: bool = False) -> str:
    """A name or title as a person reads it: no DVB emphasis marks, no
    control characters, one space between words. With *lines* (a
    description) its line breaks stay, DVB's own (0x8A) among them.
    "None" is how some boxes write an empty field."""
    s = str(value or "").replace("\x8a", "\n").replace("\r\n", "\n")
    s = _C1.sub("", _BAD_XML.sub("", s))
    if lines:
        s = re.sub(r"\n{3,}", "\n\n", "\n".join(" ".join(x.split()) for x in s.split("\n"))).strip()
    else:
        s = " ".join(s.split())
    return "" if s == "None" else s


def parse_xml(content: bytes) -> ET.Element:
    """A box's XML answer, whatever it got wrong: a declared charset that is
    not what was sent, bytes that are not UTF-8, control characters XML does
    not allow. A DOCTYPE (entities) is dropped before the parse."""
    m = _ENCODING.match(content or b"")
    declared = m.group(1).decode("ascii", "replace") if m else "utf-8"
    text = None
    for enc in dict.fromkeys((declared, "utf-8")):
        try:
            text = content.decode(enc)
            break
        except (LookupError, UnicodeDecodeError):
            continue
    if text is None:
        text = content.decode("utf-8", errors="replace")
    text = _BAD_XML.sub("", _DOCTYPE.sub("", _DECL.sub("", text.lstrip("﻿"), count=1)))
    return ET.fromstring(text)


def child_text(el: ET.Element, tag: str, lines: bool = False) -> str:
    found = el.find(tag)
    return clean_text(found.text if found is not None else "", lines)


def stable_id(key: str, taken: set[int]) -> int:
    """A channel's id from what names it on the box (a service reference,
    a uuid, a guide number), the same on every load: favourites, history
    and reminders keep pointing at the right channel."""
    sid = (zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF) or 1
    while sid in taken:
        sid = sid % 0x7FFFFFFF + 1
    taken.add(sid)
    return sid


def normalize_server(text: str, default_port: int) -> str:
    """http://host[:port][/path] from what was typed. A bare host gets the
    kind's usual port; an address typed with a scheme keeps what it says
    (a reverse proxy on port 80 is a choice, not a mistake)."""
    s = (text or "").strip().rstrip("/")
    if not s:
        return ""
    typed_scheme = "://" in s
    if not typed_scheme:
        s = "http://" + s
    u = urlsplit(s)
    netloc = u.netloc
    if not typed_scheme and u.port is None and default_port not in (80, 0):
        netloc = f"{netloc}:{default_port}"
    return urlunsplit((u.scheme or "http", netloc, u.path.rstrip("/"), "", ""))


def with_login(url: str, user: str, password: str) -> str:
    """*url* with the login in it, for the player and the logo loader: mpv
    (FFmpeg) answers both a basic and a digest challenge from it, requests
    a basic one."""
    if not user:
        return url
    u = urlsplit(url)
    host = u.netloc.rsplit("@", 1)[-1]
    auth = quote(user, safe="") + (":" + quote(password, safe="") if password else "")
    return urlunsplit((u.scheme, f"{auth}@{host}", u.path, u.query, u.fragment))


def xmltv_time(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def build_xmltv(channels: list[tuple[str, str]], events: list[dict]) -> bytes:
    """XMLTV from (id, name) channels and events with channel, start, stop
    (unix), title and optionally subtitle, desc and category."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="dopeIPTV">']
    for cid, name in channels:
        out.append(f"<channel id={quoteattr(cid)}><display-name>{escape(name)}</display-name></channel>")
    seen: set[tuple[str, int]] = set()
    for ev in sorted(events, key=lambda e: (e["channel"], e["start"])):
        key = (ev["channel"], ev["start"])
        if key in seen or ev["stop"] <= ev["start"] or not ev.get("title"):
            continue
        seen.add(key)
        parts = [f'<programme start="{xmltv_time(ev["start"])}" stop="{xmltv_time(ev["stop"])}" '
                 f'channel={quoteattr(ev["channel"])}><title>{escape(ev["title"])}</title>']
        if ev.get("subtitle"):
            parts.append(f"<sub-title>{escape(ev['subtitle'])}</sub-title>")
        if ev.get("desc"):
            parts.append(f"<desc>{escape(ev['desc'])}</desc>")
        if ev.get("category"):
            parts.append(f"<category>{escape(ev['category'])}</category>")
        parts.append("</programme>")
        out.append("".join(parts))
    out.append("</tv>\n")
    return "\n".join(out).encode("utf-8")


def _hms(secs: int) -> str:
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _number(value: Any) -> int:
    """A whole number from what a box says, 0 for anything else."""
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _day(stamp: int) -> str:
    """The date of a Unix time, "" for one no calendar here has: a box's
    garbage time (negative, or centuries off) raised on Windows and lost
    the whole source with it."""
    if stamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


class BoxClient(OfflineClient):
    """The shared half of a box on the home network. A kind gives
    ``_probe`` (is it the right kind of box: its details, or an error),
    ``_load`` (channels and recordings) and ``_guide`` (XMLTV bytes)."""

    LOG_NAME = "box"
    KIND = "box"
    DEFAULT_PORT = 80
    TIMEOUT = (6.0, 30.0)
    # A failed load is tried again after this long, not on every call.
    RETRY_SECS = 60.0
    # The box's own guide is the guide (own_guide), not a URL.
    epg_url = ""

    def __init__(self, server: str, username: str = "", password: str = "",
                 options: dict | None = None) -> None:
        super().__init__()
        self.server = normalize_server(server, self.DEFAULT_PORT)
        self.username = (username or "").strip()
        self.password = password or ""
        register_secrets(self.username, self.password,
                         quote(self.password, safe=""))
        self.options: dict[str, Any] = dict(options or {})
        self.session.headers["User-Agent"] = USER_AGENT
        self.epg_url = ""
        self.box_info: dict[str, Any] = {}
        self._auth: Any = None
        self._rows: list[dict] = []
        self._by_id: dict[int, dict] = {}
        self._recs: list[dict] = []
        self._rec_by_id: dict[int, dict] = {}
        self._loaded = False
        self._stale = False
        self._failed_at = 0.0
        self._load_lock = threading.Lock()

    # -- options --------------------------------------------------------------

    def opt(self, key: str, default: Any = None) -> Any:
        v = self.options.get(key)
        return default if v in (None, "") else v

    def opt_flag(self, key: str, default: bool) -> bool:
        v = self.options.get(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip():
            return v.strip().lower() in ("1", "true", "yes", "on")
        return default

    def opt_int(self, key: str, default: int) -> int:
        try:
            return int(str(self.options.get(key, "")).strip())
        except ValueError:
            return default

    # -- requests -------------------------------------------------------------

    def url(self, path: str) -> str:
        return self.server + (path if path.startswith("/") else "/" + path)

    def login_url(self, url: str) -> str:
        return with_login(url, self.username, self.password)

    def get(self, path: str, params: dict | None = None,
            timeout: tuple[float, float] | None = None,
            absolute: bool = False) -> Any:
        """GET from the box, logging in the way it asks (basic or digest)
        the first time it says 401. Raises LocalizedError for a login the
        box wants and does not get."""
        url = path if absolute else self.url(path)
        r = self.session.get(url, params=params, auth=self._auth,
                             timeout=timeout or self.TIMEOUT)
        if r.status_code == 401:
            if not self.username:
                raise LocalizedError("err_box_login_needed")
            if self._auth is None:
                challenge = r.headers.get("WWW-Authenticate", "").lower()
                auth_mod = requests.auth
                self._auth = (auth_mod.HTTPDigestAuth(self.username, self.password)
                              if "digest" in challenge
                              else auth_mod.HTTPBasicAuth(self.username, self.password))
                r = self.session.get(url, params=params, auth=self._auth,
                                     timeout=timeout or self.TIMEOUT)
            if r.status_code == 401:
                self._auth = None
                raise LocalizedError("err_wrong_login")
        r.raise_for_status()
        return r

    def post(self, path: str, data: dict,
             timeout: tuple[float, float] | None = None) -> Any:
        """POST a form to the box, logging in as get() does."""
        url = self.url(path)
        r = self.session.post(url, data=data, auth=self._auth,
                              timeout=timeout or self.TIMEOUT)
        if r.status_code == 401 and self.username and self._auth is None:
            challenge = r.headers.get("WWW-Authenticate", "").lower()
            auth_mod = requests.auth
            self._auth = (auth_mod.HTTPDigestAuth(self.username, self.password)
                          if "digest" in challenge
                          else auth_mod.HTTPBasicAuth(self.username, self.password))
            r = self.session.post(url, data=data, auth=self._auth,
                                  timeout=timeout or self.TIMEOUT)
        if r.status_code == 401:
            raise LocalizedError("err_wrong_login" if self.username else "err_box_login_needed")
        r.raise_for_status()
        return r

    def get_xml(self, path: str, params: dict | None = None,
                timeout: tuple[float, float] | None = None) -> ET.Element:
        return parse_xml(self.get(path, params, timeout).content)

    def get_json(self, path: str, params: dict | None = None,
                 timeout: tuple[float, float] | None = None,
                 absolute: bool = False) -> Any:
        r = self.get(path, params, timeout, absolute=absolute)
        try:
            return r.json()
        except ValueError:
            raise LocalizedError("err_unexpected_response") from None

    # -- what a kind gives ----------------------------------------------------

    def _probe(self) -> dict:
        raise NotImplementedError

    def _load(self) -> tuple[list[dict], list[dict]]:
        """(channels, recordings). A channel: key (what names it on the box),
        name, category, url, and optionally icon, epg_id, num. A recording:
        key, title, url, and optionally plot, channel, start, secs, ext."""
        raise NotImplementedError

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        return b""

    # -- the catalogue --------------------------------------------------------

    def authenticate(self) -> dict:
        if not self.server:
            raise LocalizedError("err_box_not_found", kind=self.KIND)
        try:
            self.box_info = self._probe() or {}
        except LocalizedError:
            raise
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", 0)
            if code in (404, 405, 501):
                raise LocalizedError("err_box_not_found", kind=self.KIND) from None
            raise
        except ValueError:
            raise LocalizedError("err_box_not_found", kind=self.KIND) from None
        channels, recordings = self._load()
        if not channels:
            raise LocalizedError("err_box_no_channels")
        self._take(channels, recordings)
        self._loaded = True
        self._stale = False
        log.info("%s: %d channel(s), %d recording(s) from %s", self.LOG_NAME,
                 len(self._by_id), len(self._recs), self.box_info.get("name") or "the box")
        return {"user_info": {"auth": 1}}

    def _take(self, channels: list[dict], recordings: list[dict]) -> None:
        taken: set[int] = set()
        ids: dict[str, int] = {}
        rows: list[dict] = []
        by_id: dict[int, dict] = {}
        seen: set[tuple[int, str]] = set()
        for ch in channels:
            key = str(ch.get("key") or ch.get("url") or "")
            if not key or not ch.get("url"):
                continue
            sid = ids.get(key)
            if sid is None:
                sid = ids[key] = stable_id(key, taken)
            cat = str(ch.get("category") or tr("box_channels"))
            if (sid, cat) in seen:
                continue
            seen.add((sid, cat))
            num = ch.get("num")
            row = {"stream_id": sid, "num": num if isinstance(num, int) and num > 0 else len(by_id) + 1,
                   "name": clean_text(ch.get("name")) or "?",
                   "stream_icon": str(ch.get("icon") or ""),
                   "epg_channel_id": str(ch.get("epg_id") or ""),
                   "category_id": cat, "category_name": cat,
                   "tv_archive": 0, "tv_archive_duration": 0,
                   "_url": str(ch["url"]), "_key": key}
            rows.append(row)
            by_id.setdefault(sid, row)
        recs: list[dict] = []
        rec_by_id: dict[int, dict] = {}
        rtaken: set[int] = set()
        for rec in recordings:
            key = str(rec.get("key") or rec.get("url") or "")
            if not key or not rec.get("url"):
                continue
            rid = stable_id(key, rtaken)
            secs = _number(rec.get("secs"))
            plot = clean_text(rec.get("plot"), lines=True)
            channel = clean_text(rec.get("channel"))
            start = _number(rec.get("start"))
            day = _day(start)
            if not day:
                start = 0           # no time it was made, rather than a wrong one
            info = {"plot": plot, "description": plot, "genre": channel,
                    "duration": _hms(secs) if secs else "", "duration_secs": secs,
                    "releasedate": day,
                    "cast": "", "director": "", "o_name": ""}
            row = {"num": len(recs) + 1, "name": clean_text(rec.get("title")) or "?",
                   "stream_type": "movie", "stream_id": rid, "stream_icon": str(rec.get("icon") or ""),
                   "added": str(start), "category_id": RECORDINGS, "category_ids": [],
                   "container_extension": str(rec.get("ext") or "ts").lstrip(".") or "ts",
                   "rating": "", "rating_5based": 0, "is_adult": 0,
                   "custom_sid": "", "direct_source": "", "_url": str(rec["url"]), "_info": info}
            recs.append(row)
            rec_by_id[rid] = row
        recs.sort(key=lambda r: int(r["added"] or 0), reverse=True)
        self._rows, self._by_id = rows, by_id
        self._recs, self._rec_by_id = recs, rec_by_id

    def clear_list_cache(self) -> None:
        self._stale = True
        self._failed_at = 0.0

    def _ensure(self, refresh: bool = True) -> None:
        """Load the catalogue if there is none; with *refresh* also again
        after clear_list_cache(). Play-address lookups pass refresh=False:
        they run on the window's thread and never wait on a reload."""
        if self._loaded and (not self._stale or not refresh):
            return
        with self._load_lock:
            if self._loaded and (not self._stale or not refresh):
                return
            if self._failed_at and time.monotonic() - self._failed_at < self.RETRY_SECS:
                return
            try:
                self.authenticate()
                self._failed_at = 0.0
            except Exception as e:
                self._failed_at = time.monotonic()
                log.warning("%s: loading the channels failed - %s: %s",
                            self.LOG_NAME, type(e).__name__, e)

    # -- interface ------------------------------------------------------------

    def account_info(self) -> dict:
        return dict(self.box_info)

    def live_categories(self) -> list[dict]:
        self._ensure()
        return [{"category_id": c, "category_name": c}
                for c in dict.fromkeys(r["category_id"] for r in self._rows)]

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        self._ensure()
        if category_id is None:
            return list(self._by_id.values())
        return [r for r in self._rows if r["category_id"] == category_id]

    # A play address needs the catalogue, and with none yet (the box was
    # off when the app started) loading it is a login and the box's lists -
    # seconds, or a timeout. The window asks off its thread first then
    # (MainWindow._links_later), as for a portal's links.
    slow_links = True

    def link_ready(self, method: str, ident: int | str) -> bool:
        """Whether a play address answers at once: the channels are in, or
        the last try to load them failed a moment ago (no new try yet)."""
        return self._loaded or bool(
            self._failed_at and time.monotonic() - self._failed_at < self.RETRY_SECS)

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        self._ensure(refresh=False)
        return str((self._channel(stream_id) or {}).get("_url") or "")

    def _channel(self, stream_id: int | str) -> dict | None:
        try:
            return self._by_id.get(int(stream_id))
        except (TypeError, ValueError):
            return None

    def vod_categories(self) -> list[dict]:
        self._ensure()
        return ([{"category_id": RECORDINGS, "category_name": tr("box_recordings")}]
                if self._recs else [])

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        self._ensure()
        return list(self._recs) if category_id in (None, RECORDINGS) else []

    def vod_info(self, vod_id: int | str) -> dict:
        try:
            row = self._rec_by_id.get(int(vod_id))
        except (TypeError, ValueError):
            row = None
        if row is None:
            return {}
        info = dict(row["_info"], name=row["name"], movie_image=row["stream_icon"],
                    cover_big=row["stream_icon"], rating="")
        data = {k: row[k] for k in ("stream_id", "name", "added", "category_id",
                                    "container_extension", "custom_sid", "direct_source")}
        return {"info": info, "movie_data": data}

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        self._ensure(refresh=False)
        try:
            return str((self._rec_by_id.get(int(stream_id)) or {}).get("_url") or "")
        except (TypeError, ValueError):
            return ""

    # -- the guide ------------------------------------------------------------

    def own_guide(self, progress: Callable[[int], None] | None = None) -> bytes:
        """The box's programme guide as XMLTV, for XmltvGuide: b"" when the
        box has none to give."""
        self._ensure(refresh=False)
        if not self._by_id:
            return b""
        return self._guide(progress)

    def xmltv(self) -> bytes:
        return self.own_guide()

    def guide_channels(self) -> list[tuple[str, str]]:
        """(guide id, name) of every channel that has a guide id."""
        out: dict[str, str] = {}
        for r in self._by_id.values():
            if r["epg_channel_id"]:
                out.setdefault(r["epg_channel_id"], r["name"])
        return list(out.items())
