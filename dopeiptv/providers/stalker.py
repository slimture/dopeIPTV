"""Stalker / Ministra middleware portals ("MAC portal" providers).

A portal is one JSON endpoint (portal.php, or server/load.php) that a
set-top box talks to: the box introduces itself by its MAC address, gets a
token from a handshake and sends it with every call, and every answer is
``{"js": ...}``. StalkerClient speaks it behind XtreamClient's interface and
hands out the same dict shapes, so nothing above the provider layer needs to
know which kind of provider it has.

Stream addresses are asked for (create_link) rather than built: a portal
hands out short-lived links, and a channel's own ``cmd`` often only names
it ("ffmpeg http://localhost/ch/12_").

The archive (catch-up) is kept by programme: a channel row flags it
(tv_archive / archive / enable_tv_archive, its depth in hours), the guide
table (get_simple_data_table) lists the programmes with their ids, and
create_link with type=tv_archive and cmd "auto /media/<programme id>.mpg"
gives a link that plays one programme from its start (archive_link).
"""
from __future__ import annotations

import base64
import calendar
import json
import math
import re
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from ..core._lazy_requests import requests
from ..core.log import log, register_secrets
from ..core.privacy import for_log
from ..i18n import LocalizedError, tr
from .client import ListCache, json_object

# What the portal sees is a MAG250 set-top box: portals serve nothing to a
# player they do not know, and some check the firmware get_profile reports.
USER_AGENT = ("Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
              "(KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 "
              "Safari/533.3")
X_USER_AGENT = "Model: MAG250; Link: WiFi"
_FIRMWARE = ("ImageDescription: 0.2.18-r23-250; ImageDate: Fri Jan 15 "
             "15:20:44 EET 2016; PORTAL version: 5.6.1; API Version: JS API "
             "version: 343; STB API version: 146; Player Engine version: "
             "0x58c")

_API_TAILS = ("/portal.php", "/server/load.php")
_AUTH_FAILED = re.compile(r"authori[sz]ation\s+failed|unauthori[sz]ed", re.I)
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://\S+$", re.I)
_VIDEO_EXTS = frozenset(("mkv", "mp4", "avi", "mov", "m4v", "webm", "ts",
                         "flv"))
# How portals write an account's end date (get_main_info's "phone" is a
# favourite place for it, in words).
_EXPIRY_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                   "%d.%m.%Y %H:%M", "%d.%m.%Y", "%d/%m/%Y",
                   "%B %d, %Y, %I:%M %p", "%B %d, %Y %I:%M %p", "%B %d, %Y",
                   "%b %d, %Y", "%d %B %Y")
_SERIES_INFO_KEYS = ("name", "cover", "plot", "cast", "director", "genre",
                     "releaseDate", "last_modified", "rating",
                     "rating_5based", "backdrop_path", "youtube_trailer",
                     "episode_run_time", "category_id")
# Where a channel row says it has an archive: Ministra's lists have
# "archive" and "enable_tv_archive", other portals "tv_archive".
_ARCHIVE_FLAGS = ("tv_archive", "archive", "enable_tv_archive")
# A MAG player's options after the address in a cmd ("position:0").
_MAG_OPTIONS = re.compile(r"(?:\s+[a-z_]+:(?!//)\S*)+\s*$", re.I)


def portal_endpoints(url: str) -> list[str]:
    """The API endpoints a portal address may stand for, likeliest first.

    Providers hand out the address a box opens (http://host:port/c/,
    http://host/stalker_portal/c/), not the API's: that is portal.php or
    server/load.php beside it, with or without /stalker_portal. An address
    that already names one of them is tried first."""
    s = (url or "").strip()
    if not s:
        return []
    if "://" not in s:
        s = "http://" + s
    try:
        parts = urlsplit(s)
    except ValueError:
        return []
    if not parts.netloc:
        return []
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    out: list[str] = []
    for tail in _API_TAILS:
        if path.lower().endswith(tail):
            out.append(origin + path)
            path = path[:-len(tail)]
            break
    else:
        # The box's own page: /c, /c/index.html.
        path = re.sub(r"/c(?:/[^/]*)?$", "", path, flags=re.I)
    path = path.rstrip("/")
    if path.lower().endswith("/stalker_portal"):
        bases = [path, path[:-len("/stalker_portal")]]
    else:
        bases = [path, path + "/stalker_portal"]
    for base in bases:
        for tail in _API_TAILS:
            if origin + base + tail not in out:
                out.append(origin + base + tail)
    return out


def api_base(endpoint: str) -> str:
    """The portal's root for an API endpoint: what the box's page (/c/),
    the logos and the Referer hang off."""
    for tail in _API_TAILS:
        if endpoint.endswith(tail):
            return endpoint[:-len(tail)]
    return endpoint.rstrip("/")


def normalize_mac(mac: str) -> str:
    """00:1A:79:12:34:56 however it was typed (dashes, no separators, lower
    case). Anything that is not six bytes is kept as typed."""
    digits = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    if len(digits) != 12:
        return (mac or "").strip().upper()
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2)).upper()


def strip_cmd(cmd: Any) -> str:
    """A portal cmd as a plain address: it names the player to use first
    ("ffmpeg http://...", "auto http://...")."""
    s = str(cmd or "").strip()
    head, _, rest = s.partition(" ")
    if rest and "://" not in head:
        s = rest.strip()
    return s


def _direct_url(s: str) -> bool:
    """Whether *s* plays as it is: http(s) on a real host, not the
    localhost placeholder a cmd uses to name a channel."""
    try:
        parts = urlsplit(s)
        host = parts.hostname or ""
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and host not in (
        "", "localhost", "127.0.0.1")


def _not_offered(e: Exception) -> bool:
    """An HTTP error saying the portal has no such call - as opposed to
    one that is struggling, whose lists should come from the cache."""
    resp = getattr(e, "response", None)
    return getattr(resp, "status_code", None) in (400, 404, 405, 501)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        return 0


def _float(value: Any) -> float:
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and abs(f) != float("inf") else 0.0


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _id(value: Any) -> int | str | None:
    """A portal id as XtreamClient's: an int where it is a number."""
    s = "" if value is None else str(value).strip()
    if not s:
        return None
    return int(s) if s.isdigit() else s


def _numbers(value: Any) -> list[int]:
    """The episode numbers a portal lists on a series or a season."""
    if not isinstance(value, list):
        return []
    return [n for n in (_int(x) for x in value) if n > 0]


def _b64(text: str) -> str:
    """Xtream's EPG text encoding, so b64() reads both providers alike."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _utc_stamp(value: Any) -> int | None:
    """A portal time as unix time. The cookie asks for UTC, so a plain
    "YYYY-MM-DD HH:MM:SS" is read as UTC."""
    s = str(value or "").strip()
    if s.isdigit():
        return int(s) or None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return calendar.timegm(time.strptime(s, fmt))
        except ValueError:
            continue
    return None


def _expiry(value: Any) -> int | None:
    """An account's end date as unix time, or None for none/unlimited."""
    s = str(value or "").strip()
    if s.isdigit():
        n = int(s)
        return n if n > 10 ** 8 else None
    for fmt in _EXPIRY_FORMATS:
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except (ValueError, OverflowError, OSError):
            continue
    return None


def _local(stamp: int) -> str:
    try:
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return ""


def _hms(secs: int) -> str:
    if secs <= 0:
        return ""
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _container(cmd: str) -> str:
    """A title's container from its address where that says, else mp4
    (the portal's link decides what really plays)."""
    path = strip_cmd(cmd).split("?", 1)[0]
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext if ext in _VIDEO_EXTS else "mp4"


def _rating(row: dict) -> str:
    for key in ("rating_imdb", "rating_kinopoisk", "rating"):
        s = str(row.get(key) or "").strip()
        if _float(s) > 0:
            return s
    return ""


def _episode_key(series: str, season: int, num: int) -> str:
    """An id for an episode the portal only numbers. The window keeps
    watched episodes by int(id), so it must be a number, and one no other
    series' episode has."""
    base = (int(series) if series.isdigit()
            else zlib.crc32(series.encode("utf-8")) & 0x7FFFFFFF)
    return str(base * 10 ** 7 + min(season, 999) * 10 ** 4 + min(num, 9999))


def _is_season(row: dict, series_id: str) -> bool:
    if _flag(row.get("is_season")):
        return True
    # Seasons that carry their episode numbers, unless the portal ignored
    # movie_id and answered with a page of series.
    return (bool(_numbers(row.get("series")))
            and not _flag(row.get("is_series"))
            and str(row.get("id")) != series_id)


def _is_episode(row: dict) -> bool:
    return _flag(row.get("is_episode")) or "series_number" in row


def _archive_days(ch: dict) -> int:
    """A channel's archive in whole days (what the window counts in), 0
    without one. Portals flag it three ways; the depth is in hours, as
    Ministra's admin sets it, and an archive of unknown depth is a day."""
    if not any(_flag(ch.get(k)) for k in _ARCHIVE_FLAGS):
        return 0
    hours = _float(ch.get("tv_archive_duration"))
    return max(1, math.ceil(hours / 24)) if hours > 0 else 1


def archive_address(answer: Any) -> str:
    """The address in a tv_archive create_link answer's cmd, or "". It
    names the player first, as every cmd does, and a box's player options
    may follow it ("auto http://... position:0 media_len:3600") - the
    archive link begins at the programme's start either way."""
    got = answer.get("cmd") if isinstance(answer, dict) else answer
    s = strip_cmd(got if isinstance(got, str) else "")
    s = _MAG_OPTIONS.sub("", s).strip().replace(" ", "%20")
    return s if _URL.match(s) else ""


def archive_cmds(row: dict) -> list[str]:
    """What create_link may be asked to play a programme from the portal's
    guide, likeliest first: the programme's own cmd where the portal gives
    one, then the name Ministra gives it by its id, then by its real_id
    (<channel>_<start>), which other portals key their archive by."""
    out: list[str] = []
    own = _text(row.get("cmd"))
    if own:
        out.append(own)
    for ident in (_text(row.get("id")), _text(row.get("real_id"))):
        cmd = f"auto /media/{ident}.mpg"
        if ident and cmd not in out:
            out.append(cmd)
    return out


def _archive_rows(rows: list[dict]) -> list[dict]:
    """A guide table's programmes as {"start", "stop", "row"}, by start. A
    programme without an end runs to the next one's start."""
    out: list[dict[str, Any]] = []
    for p in rows:
        start = (_int(p.get("start_timestamp")) or _utc_stamp(p.get("time"))
                 or 0)
        if start <= 0:
            continue
        stop = (_int(p.get("stop_timestamp")) or _utc_stamp(p.get("time_to"))
                or start + _int(p.get("duration")))
        out.append({"start": start, "stop": stop, "row": p})
    out.sort(key=lambda r: r["start"])
    for here, nxt in zip(out, out[1:], strict=False):
        if here["stop"] <= here["start"]:
            here["stop"] = nxt["start"]
    return [r for r in out if r["stop"] > r["start"]]


def pick_programme(rows: list[dict], start: float,
                   stop: float | None = None) -> dict | None:
    """The programme of *rows* (_archive_rows) to play from *start*: the
    one on at that moment - or, given the *stop* of a programme picked in
    a guide, the one overlapping it most, so a guide whose times differ
    from the portal's by a minute or two still finds the same programme."""
    if stop is not None and stop > start:
        best, most = None, 0.0
        for r in rows:
            overlap = min(stop, r["stop"]) - max(start, r["start"])
            if overlap > most:
                best, most = r, overlap
        return best
    return next((r for r in rows if r["start"] <= start < r["stop"]), None)


class StalkerClient(ListCache):
    """A Stalker / Ministra middleware portal, addressed by the portal URL
    and the box's MAC address, behind XtreamClient's interface."""

    LOG_NAME = "stalker"
    # See XtreamClient.NET_COOLDOWN_SECS.
    NET_COOLDOWN_SECS = 30
    # live_url / vod_url / episode_url run on the window's thread when
    # something starts playing: every call behind one link, a new handshake
    # included, shares this much time.
    LINK_BUDGET_SECS = 8.0
    # The player, the recorder and the cast dialog ask for the same title
    # within moments of each other; one link serves them all.
    LINK_CACHE_SECS = 30.0
    # A link the portal did not give is not asked for again this soon: the
    # window asks once off its thread, then once more to play.
    LINK_FAIL_SECS = 5.0
    # The window fetches this client's play addresses off its thread first
    # (see MainWindow._links_later).
    slow_links = True
    # A portal's archive is played by programme (archive_link): the window
    # asks for it off its thread, and timeshift_urls has nothing.
    archive_by_programme = True
    # Every create_link behind one archive programme shares this much time
    # (archive_link runs off the window's thread).
    ARCHIVE_BUDGET_SECS = 15.0
    # How long a day of a channel's guide stays in memory for the archive:
    # today's changes as programmes end and enter it.
    ARCHIVE_TODAY_SECS = 300.0
    ARCHIVE_PAST_SECS = 1800.0
    # How many days back the archive's programme list reads at most.
    ARCHIVE_TABLE_DAYS = 7
    # Pages are small (14 rows is common): this is 2,800 rows a list.
    MAX_PAGES = 200
    EPG_PAGES = 10
    SEASON_PAGES = 20
    # How many played films and opened episodes keep what playing them
    # needs, across sessions.
    KNOWN_MAX = 3000
    _KNOWN_KEY = ("known_links",)
    # The list cache keys (their first part) whose rows are channels, and
    # those whose rows are films or series.
    _LIVE_KEYS = ("itv_all", "itv_genre")
    _TITLE_KEYS = ("vod", "series")

    def __init__(self, server: str, mac: str,
                 cache_path: str | None = None) -> None:
        s = (server or "").strip()
        if s and "://" not in s:
            s = "http://" + s
        self.server = s.rstrip("/")
        self.mac = normalize_mac(mac)
        # Interface parity: the window and the guide read these off any
        # client. There is no account name or password, only the MAC.
        self.username = ""
        self.password = ""
        # A portal rarely serves XMLTV: an empty epg_url tells the guide
        # there is no provider guide to fetch, as for an M3U playlist (a
        # playlist's own guide URL still wins).
        self.epg_url = ""
        self.max_connections: int | None = None
        # The MAC is the account: masked out of every log record, in the
        # shapes it travels in.
        register_secrets(self.mac, self.mac.lower(), quote(self.mac, safe=""))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT,
                                     "X-User-Agent": X_USER_AGENT})
        super().__init__(cache_path)
        self._endpoints = portal_endpoints(self.server)
        self._endpoint: str | None = None
        self._auth: tuple[str, str] | None = None     # (endpoint, token)
        self._profile: dict = {}
        self._auth_lock = threading.Lock()
        self._net_down_until = 0.0
        self._links: dict[str, tuple[float, str]] = {}
        self._failed: dict[str, float] = {}
        self._link_lock = threading.Lock()
        self._known: dict[str, dict] | None = None
        self._known_lock = threading.Lock()
        # (channel, YYYY-MM-DD) -> (when, the day's guide rows): archive_link
        # and archive_table read the same days.
        self._days: dict[tuple[str, str], tuple[float, list[dict]]] = {}

    # -- requests -------------------------------------------------------------

    def _headers(self, endpoint: str, token: str | None) -> dict[str, str]:
        h = {"Referer": api_base(endpoint) + "/c/",
             "Cookie": f"mac={self.mac}; stb_lang=en; timezone=UTC"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _redact(self, text: str) -> str:
        """The MAC and the token out of a line before it is logged - also
        where the log filter is not installed."""
        token = self._auth[1] if self._auth else ""
        for secret in (self.mac, self.mac.lower(), quote(self.mac, safe=""),
                       token):
            if secret and len(secret) > 3:
                text = text.replace(secret, "***")
        return text

    @staticmethod
    def _timeout(read: float, deadline: float | None) -> tuple[float, float]:
        if deadline is None:
            return (10.0, read)
        left = deadline - time.monotonic()
        if left < 0.5:
            raise requests.Timeout("no time left for this link")
        return (min(5.0, left), min(read, left))

    def _request(self, endpoint: str, params: dict[str, Any],
                 token: str | None,
                 timeout: tuple[float, float]) -> tuple[Any, bool]:
        """GET one call: (the parsed reply, whether the portal refused the
        token). A network failure starts the fail-fast cooldown; any other
        HTTP error raises as it is."""
        what = f"{params.get('type')}/{params.get('action')}"
        t0 = time.monotonic()
        if t0 < self._net_down_until:
            raise requests.ConnectionError(tr(
                "err_provider_cooldown",
                secs=f"{self._net_down_until - t0:.0f}"))
        query = dict(params, JsHttpRequest="1-xml")
        try:
            r = self.session.get(endpoint, params=query, timeout=timeout,
                                 headers=self._headers(endpoint, token))
        except (requests.Timeout, requests.ConnectionError) as e:
            self._net_down_until = time.monotonic() + self.NET_COOLDOWN_SECS
            log.warning("stalker %s @ %s: request failed - %s: %s "
                        "(failing fast for the next %d s)", what, self.server,
                        type(e).__name__, self._redact(str(e)),
                        self.NET_COOLDOWN_SECS)
            raise
        except requests.RequestException as e:
            log.warning("stalker %s @ %s: request failed - %s: %s", what,
                        self.server, type(e).__name__, self._redact(str(e)))
            raise
        self._net_down_until = 0.0
        ms = (time.monotonic() - t0) * 1000
        if r.status_code in (401, 403):
            log.info("stalker %s @ %s: HTTP %s (%.0f ms)", what, self.server,
                     r.status_code, ms)
            return None, True
        if r.status_code != 200:
            log.warning("stalker %s @ %s: HTTP %s (%.0f ms)", what,
                        self.server, r.status_code, ms)
            r.raise_for_status()
        try:
            data = r.json(object_pairs_hook=json_object)
        except ValueError:
            # Ministra answers an expired token with a bare text line.
            if _AUTH_FAILED.search(r.text[:300]):
                return None, True
            log.warning("stalker %s @ %s: non-JSON reply (content-type=%s, "
                        "%d bytes)", what, self.server,
                        r.headers.get("content-type", "?"), len(r.content))
            raise
        if (isinstance(data, dict) and "js" not in data
                and _AUTH_FAILED.search(str(data.get("text")
                                            or data.get("error") or ""))):
            return None, True
        log.debug("stalker %s @ %s: HTTP 200 (%.0f ms, %d bytes)", what,
                  self.server, ms, len(r.content))
        return data, False

    def _call(self, params: dict[str, Any], read: float = 20.0,
              deadline: float | None = None) -> Any:
        """One portal call, logged in first: the reply's js. A refused
        token is renewed (tokens expire) and the call made once more."""
        endpoint, token = self._logged_in(deadline)
        for again in (False, True):
            data, refused = self._request(endpoint, params, token,
                                          self._timeout(read, deadline))
            if not refused:
                return data.get("js") if isinstance(data, dict) else None
            if again:
                break
            log.info("stalker @ %s: the token was refused - handshaking "
                     "again", self.server)
            endpoint, token = self._logged_in(deadline, stale=token)
        log.warning("stalker %s/%s @ %s: refused after a new handshake",
                    params.get("type"), params.get("action"), self.server)
        raise LocalizedError("err_wrong_login")

    def _logged_in(self, deadline: float | None,
                   stale: str | None = None) -> tuple[str, str]:
        """(endpoint, token): logged in first when there is no token yet,
        or when it is *stale* - unless another thread renewed it already."""
        auth = self._auth
        if auth is not None and auth[1] != stale:
            return auth
        wait = -1.0 if deadline is None else max(0.0,
                                                 deadline - time.monotonic())
        if not self._auth_lock.acquire(timeout=wait):
            raise requests.Timeout("still logging in to the portal")
        try:
            auth = self._auth
            if auth is None or auth[1] == stale:
                self._auth = None
                auth = self._login(deadline)
            return auth
        finally:
            self._auth_lock.release()

    def _login(self, deadline: float | None) -> tuple[str, str]:
        """(under _auth_lock) The handshake and the box's profile: a token
        the portal accepts for this MAC. LocalizedError("err_wrong_login")
        when it does not know the box or has blocked it."""
        endpoint, token, random = self._handshake(deadline)
        profile = self._get_profile(endpoint, token, random, deadline)
        self._check_profile(profile)
        register_secrets(token)
        self._endpoint, self._profile = endpoint, profile
        self.max_connections = _int(profile.get("max_connections")) or None
        self._auth = (endpoint, token)
        log.info("stalker auth @ %s: ok via %s", self.server,
                 urlsplit(endpoint).path)
        return self._auth

    def _handshake(self, deadline: float | None) -> tuple[str, str, str]:
        """(endpoint, token, random) from the endpoint found before, else
        from the first candidate that answers a handshake."""
        cands = list(self._endpoints)
        if self._endpoint in cands:
            cands.remove(self._endpoint)
            cands.insert(0, self._endpoint)
        params = {"type": "stb", "action": "handshake", "token": ""}
        refused = False
        for ep in cands:
            try:
                data, no = self._request(ep, params, None,
                                         self._timeout(15.0, deadline))
            except (requests.Timeout, requests.ConnectionError):
                raise           # the host itself is down: so is every path
            except (requests.RequestException, ValueError):
                continue        # no portal API at this path
            refused = refused or no
            js = data.get("js") if isinstance(data, dict) else None
            token = str(js.get("token") or "") if isinstance(js, dict) else ""
            if token and isinstance(js, dict):
                if ep != self._endpoint:
                    log.info("stalker @ %s: the portal API is %s",
                             self.server, urlsplit(ep).path)
                return ep, token, str(js.get("random") or "")
        if refused:
            log.warning("stalker handshake @ %s: refused", self.server)
            raise LocalizedError("err_wrong_login")
        log.warning("stalker handshake @ %s: no portal API answers (tried "
                    "%s)", self.server,
                    ", ".join(urlsplit(c).path for c in cands) or "nothing")
        raise LocalizedError("err_unexpected_response")

    def _get_profile(self, endpoint: str, token: str, random: str,
                     deadline: float | None) -> dict:
        params: dict[str, Any] = {
            "type": "stb", "action": "get_profile", "hd": "1",
            "ver": _FIRMWARE, "num_banks": "2", "sn": "",
            "stb_type": "MAG250", "image_version": "218",
            "video_out": "hdmi", "device_id": "", "device_id2": "",
            "signature": "", "auth_second_step": "1",
            "hw_version": "1.7-BD-00", "not_valid_token": "0"}
        if random:
            params["metrics"] = json.dumps({
                "mac": self.mac, "sn": "", "model": "MAG250", "type": "STB",
                "uid": "", "random": random})
        data, refused = self._request(endpoint, params, token,
                                      self._timeout(20.0, deadline))
        if refused:
            log.warning("stalker auth @ %s: the profile was refused",
                        self.server)
            raise LocalizedError("err_wrong_login")
        js = data.get("js") if isinstance(data, dict) else None
        return js if isinstance(js, dict) else {}

    def _check_profile(self, profile: dict) -> None:
        if not profile:
            why = "no profile"
        elif _flag(profile.get("blocked")):
            why = "blocked"
        elif _int(profile.get("status")) != 0:
            why = f"status {profile.get('status')}"
        elif "id" in profile and not profile.get("id"):
            why = "the portal does not know this box"
        else:
            return
        said = str(profile.get("block_msg") or profile.get("msg") or "")
        log.warning("stalker auth @ %s: rejected - %s%s", self.server, why,
                    f" ({self._redact(said.strip())})" if said.strip() else "")
        raise LocalizedError("err_wrong_login")

    def _pages(self, params: dict[str, Any],
               limit: int | None = None) -> list[dict]:
        """Every row of a paged list (get_ordered_list and friends), page
        by page, for at most *limit* (MAX_PAGES) pages."""
        rows: list[dict] = []
        seen: set[str] = set()
        cap = limit or self.MAX_PAGES
        for p in range(1, cap + 1):
            try:
                js = self._call(dict(params, p=p), read=30.0)
            except requests.HTTPError as e:
                if rows and _not_offered(e):
                    break
                raise
            data = js.get("data") if isinstance(js, dict) else js
            page = [d for d in data if isinstance(d, dict)] \
                if isinstance(data, list) else []
            if not page:
                break
            # A portal that ignores p answers the first page again and again.
            mark = f"{page[0].get('id')}\x1f{page[0].get('name')}"
            if mark in seen:
                break
            seen.add(mark)
            rows.extend(page)
            if not isinstance(js, dict):
                break           # a bare list is not paged
            total = _int(js.get("total_items"))
            per = _int(js.get("max_page_items"))
            if (total and len(rows) >= total) or (
                    not total and per and len(page) < per):
                break
        else:
            log.warning("stalker %s/%s @ %s: stopped after %d pages",
                        params.get("type"), params.get("action"),
                        self.server, cap)
        return rows

    def _find(self, kinds: tuple[str, ...], field: str,
              value: Any) -> dict | None:
        """A row whose *field* is *value* in a list fetched before (memory
        only), among the lists whose cache key starts with one of *kinds*."""
        want = str(value)
        with self._list_lock:
            lists = [data for key, (_t, data) in self._list_cache.items()
                     if key and key[0] in kinds]
        for rows in lists:
            for row in rows:
                if isinstance(row, dict) and str(row.get(field)) == want:
                    return row
        return None

    def _root(self) -> str:
        ep = self._endpoint or (self._endpoints[0] if self._endpoints
                                else self.server)
        return api_base(ep)

    def _image(self, value: Any) -> str:
        s = str(value or "").strip()
        if not s or "://" in s:
            return s
        root = self._root()
        if s.startswith("/"):
            p = urlsplit(root)
            return f"{p.scheme}://{p.netloc}{s}"
        return f"{root}/{s}"

    def _logo(self, value: Any) -> str:
        s = str(value or "").strip()
        if s and "/" not in s:
            # Ministra names a logo by file; the box fetches it by size.
            return f"{self._root()}/misc/logos/320/{s}"
        return self._image(s)

    # -- account --------------------------------------------------------------

    def authenticate(self) -> dict:
        """The portal's login - handshake and profile - answered like
        XtreamClient.authenticate: {"user_info": {..., "auth": 1},
        "server_info": {...}}. Raises LocalizedError("err_wrong_login")
        for a box the portal does not know or has blocked."""
        self._logged_in(None)
        return {"user_info": self._user_info({}),
                "server_info": self._server_info()}

    def account_info(self) -> dict:
        """The account as XtreamClient.account_info has it: user_info
        (status, exp_date as unix time when the portal gives an end date,
        max_connections when it says) plus server_info. {} on any error
        rather than raising."""
        try:
            self._logged_in(None)
        except Exception as e:
            log.warning("stalker account_info @ %s failed: %s: %s",
                        self.server, type(e).__name__, self._redact(str(e)))
            return {}
        main: Any = {}
        try:
            main = self._call({"type": "account_info",
                               "action": "get_main_info"})
        except Exception as e:
            log.info("stalker get_main_info @ %s: nothing - %s: %s",
                     self.server, type(e).__name__, self._redact(str(e)))
        main = main if isinstance(main, dict) else {}
        maxc = _int(main.get("max_connections"))
        if maxc > 0:
            self.max_connections = maxc
        return {"user_info": self._user_info(main),
                "server_info": self._server_info()}

    def _user_info(self, main: dict) -> dict:
        prof = self._profile
        exp = None
        for src, key in ((main, "end_date"), (main, "expire_billing_date"),
                         (prof, "expire_billing_date"), (prof, "end_date"),
                         (main, "phone")):
            exp = _expiry(src.get(key))
            if exp:
                break
        info: dict[str, Any] = {
            "auth": 1, "username": str(prof.get("login") or ""),
            "status": "Active", "exp_date": str(exp) if exp else None,
            "is_trial": "0", "message": ""}
        if self.max_connections:
            info["max_connections"] = str(self.max_connections)
        return info

    def _server_info(self) -> dict:
        p = urlsplit(self._endpoint or self.server)
        try:
            port = p.port or (443 if p.scheme == "https" else 80)
        except ValueError:
            port = 0
        return {"url": p.hostname or "", "port": str(port),
                "server_protocol": p.scheme,
                "timezone": str(self._profile.get("default_timezone") or ""),
                "timestamp_now": int(time.time())}

    # -- lists ----------------------------------------------------------------

    def _categories(self, kind: str, action: str) -> list[dict]:
        js = self._call({"type": kind, "action": action})
        rows = js.get("data") if isinstance(js, dict) else js
        out = []
        for c in rows if isinstance(rows, list) else []:
            cid = _text(c.get("id")) if isinstance(c, dict) else ""
            # "*" is the portal's own "All": the window adds its own.
            if not cid or cid == "*":
                continue
            out.append({"category_id": cid,
                        "category_name": str(c.get("title") or c.get("name")
                                             or cid).strip(),
                        "parent_id": 0})
        return out

    def live_categories(self) -> list[dict]:
        return self._cached_list(
            ("itv_genres",), lambda: self._categories("itv", "get_genres"))

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        rows = self._cached_list(("itv_all",), self._all_channels)
        if rows:
            if category_id is None:
                return rows
            return [r for r in rows if r["category_id"] == str(category_id)]
        # No get_all_channels on this portal: the genre's pages instead.
        genre = "*" if category_id is None else str(category_id)
        return self._cached_list(("itv_genre", genre), lambda: self._live_rows(
            self._pages({"type": "itv", "action": "get_ordered_list",
                         "genre": genre, "fav": "0", "sortby": "number"})))

    def _all_channels(self) -> list[dict]:
        try:
            js = self._call({"type": "itv", "action": "get_all_channels"},
                            read=60.0)
        except requests.HTTPError as e:
            if _not_offered(e):
                return []
            raise
        data = js.get("data") if isinstance(js, dict) else js
        return self._live_rows(data)

    def _live_rows(self, data: Any) -> list[dict]:
        out: list[dict] = []
        for ch in data if isinstance(data, list) else []:
            sid = _id(ch.get("id")) if isinstance(ch, dict) else None
            if sid is None:
                continue
            cmd = str(ch.get("cmd") or "")
            cmds = ch.get("cmds")
            if not cmd and isinstance(cmds, list) and cmds \
                    and isinstance(cmds[0], dict):
                cmd = str(cmds[0].get("url") or "")
            cat = _text(ch.get("tv_genre_id"))
            days = _archive_days(ch)
            out.append({
                "num": _int(ch.get("number")) or len(out) + 1,
                "name": str(ch.get("name") or "").strip() or "?",
                "stream_type": "live", "stream_id": sid,
                "stream_icon": self._logo(ch.get("logo")),
                "epg_channel_id": str(ch.get("xmltv_id") or ""),
                "added": "0", "is_adult": int(_flag(ch.get("censored"))),
                "category_id": cat,
                "category_ids": [int(cat)] if cat.isdigit() else [],
                "custom_sid": "", "direct_source": "",
                # Played by programme (archive_link), in whole days as
                # Xtream counts.
                "tv_archive": 1 if days else 0, "tv_archive_duration": days,
                # What create_link needs to play the channel.
                "_cmd": cmd})
        return out

    def vod_categories(self) -> list[dict]:
        return self._cached_list(
            ("vod_categories",), lambda: self._categories("vod",
                                                          "get_categories"))

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        return [r for r in self._titles("vod", category_id)
                if "stream_id" in r]

    def series_categories(self) -> list[dict]:
        if self._series_module() == "series":
            return self._cached_list(
                ("series_categories",),
                lambda: self._categories("series", "get_categories"))
        return self.vod_categories()

    def series_list(self, category_id: str | None = None) -> list[dict]:
        return [r for r in self._titles(self._series_module(), category_id)
                if "series_id" in r]

    def _series_module(self) -> str:
        """Where the portal keeps series: a module of their own
        (type=series), or among the films with is_series=1 (type=vod)."""
        got = self._cached_list(("series_module",), self._detect_series)
        return "series" if got and got[0] == "series" else "vod"

    def _detect_series(self) -> list[str]:
        try:
            js = self._call({"type": "series", "action": "get_categories"})
        except requests.HTTPError as e:
            if _not_offered(e):
                return ["vod"]
            raise
        except (ValueError, LocalizedError):
            # No JSON, or a module this box may not use: either way the
            # series are among the films. A blocked box shows elsewhere.
            return ["vod"]
        rows = js.get("data") if isinstance(js, dict) else js
        has = isinstance(rows, list) and any(
            isinstance(c, dict) and str(c.get("id") or "*") != "*"
            for c in rows)
        return ["series" if has else "vod"]

    def _titles(self, module: str, category_id: str | None) -> list[dict]:
        """A category's films and series rows (the same pages hold both
        on a portal without a series module)."""
        cat = "*" if category_id is None else str(category_id)
        return self._cached_list((module, "list", cat), lambda: (
            self._title_rows(module, cat, self._pages({
                "type": module, "action": "get_ordered_list",
                "category": cat, "genre": "*", "sortby": "added",
                "fav": "0", "hd": "0", "not_ended": "0", "abc": "*",
                "years": "*"}))))

    def _title_rows(self, module: str, category: str,
                    data: list[dict]) -> list[dict]:
        out: list[dict] = []
        for v in data:
            vid = _id(v.get("id"))
            if vid is None:
                continue
            cat = (_text(v.get("category_id"))
                   or ("" if category == "*" else category))
            rating = _rating(v)
            image = self._image(v.get("screenshot_uri") or v.get("cover")
                                or v.get("poster"))
            added = str(_utc_stamp(v.get("added")) or 0)
            cmd = str(v.get("cmd") or "")
            secs = _int(v.get("time")) * 60
            year = str(v.get("year") or "").strip()
            row: dict[str, Any] = {
                "num": len(out) + 1,
                "name": str(v.get("name") or v.get("o_name") or "").strip()
                or "?",
                "rating": rating,
                "rating_5based": round(_float(rating) / 2, 1),
                "category_id": cat,
                "category_ids": [int(cat)] if cat.isdigit() else []}
            details: dict[str, Any] = {
                "plot": str(v.get("description") or "").strip(),
                "cast": str(v.get("actors") or "").strip(),
                "director": str(v.get("director") or "").strip(),
                "genre": str(v.get("genres_str") or "").strip()}
            if module == "series" or _flag(v.get("is_series")):
                row.update(details, series_id=vid, cover=image,
                           releaseDate=year, last_modified=added,
                           backdrop_path=[], youtube_trailer="",
                           episode_run_time=str(secs // 60 or ""),
                           _cmd=cmd, _series=_numbers(v.get("series")))
            else:
                details.update(
                    o_name=str(v.get("o_name") or "").strip(),
                    description=details["plot"], actors=details["cast"],
                    releasedate=year, duration=_hms(secs),
                    duration_secs=secs,
                    country=str(v.get("country") or "").strip(),
                    age=str(v.get("age") or "").strip())
                row.update(stream_type="movie", stream_id=vid,
                           stream_icon=image, added=added,
                           is_adult=int(_flag(v.get("censored"))),
                           container_extension=_container(cmd),
                           custom_sid="", direct_source="",
                           # vod_info's answer: a portal's film list carries
                           # what Xtream keeps for get_vod_info.
                           _cmd=cmd, _info=details)
            out.append(row)
        return out

    def vod_info(self, vod_id: int | str) -> dict:
        """A film's details as get_vod_info has them ({"info", "movie_data"}),
        from its row in a list fetched before. {} for a film no such list
        holds - the portal has no call for one film."""
        row = self._find(("vod",), "stream_id", vod_id)
        if row is None:
            return {}
        info = dict(row.get("_info") or {})
        info.update(name=row["name"], movie_image=row["stream_icon"],
                    cover_big=row["stream_icon"], rating=row["rating"])
        data = {k: row[k] for k in ("stream_id", "name", "added",
                                    "category_id", "container_extension",
                                    "custom_sid", "direct_source")}
        return {"info": info, "movie_data": data}

    # -- series ---------------------------------------------------------------

    def series_info(self, series_id: int | str) -> dict:
        """Seasons and episodes as get_series_info has them: {"seasons",
        "info", "episodes": {"1": [...], ...}}. Portals keep a series three
        ways - its episode numbers on the series itself, seasons that carry
        their episode numbers, or seasons, episodes and files apart
        (Ministra 5) - and all three are read. A step the portal does not
        offer gives nothing rather than an error."""
        sid = str(series_id)
        show = self._find(self._TITLE_KEYS, "series_id", series_id) or {}
        module = self._series_module()
        # (season, name, [(episode id, number, its row, what plays it)])
        seasons: list[tuple[int, str, list[tuple[str, int, dict, dict]]]] = []
        nums = _numbers(show.get("_series"))
        if nums:
            cmd = str(show.get("_cmd") or "")
            seasons.append((1, "", [(_episode_key(sid, 1, n), n, {},
                                     {"cmd": cmd, "series": n})
                                    for n in nums]))
        else:
            found = [r for r in self._drill(module, sid) if _is_season(r, sid)]
            for i, srow in enumerate(found, 1):
                seasons.append(self._season(module, sid, i, srow))
        episodes: dict[str, list[dict]] = {}
        out_seasons: list[dict] = []
        known: dict[str, dict] = {}
        for snum, name, eps in seasons:
            if not eps:
                continue
            group = episodes.setdefault(str(snum), [])
            for eid, n, erow, rec in eps:
                group.append(self._episode(eid, snum, n, erow, show))
                known[f"e{eid}"] = rec
            out_seasons.append({"season_number": snum, "name": name,
                                "episode_count": len(group), "id": snum,
                                "cover": "", "cover_big": "", "overview": "",
                                "air_date": ""})
        if known:
            self._remember(known)
        info = {k: show[k] for k in _SERIES_INFO_KEYS if k in show}
        return {"seasons": out_seasons, "info": info, "episodes": episodes}

    def _drill(self, module: str, movie_id: str, season_id: str = "0",
               episode_id: str = "0") -> list[dict]:
        try:
            return self._pages({"type": module, "action": "get_ordered_list",
                                "movie_id": movie_id, "season_id": season_id,
                                "episode_id": episode_id},
                               limit=self.SEASON_PAGES)
        except requests.HTTPError as e:
            if _not_offered(e):
                return []
            raise
        except ValueError:
            return []

    def _season(self, module: str, sid: str, index: int, srow: dict
                ) -> tuple[int, str, list[tuple[str, int, dict, dict]]]:
        name = str(srow.get("name") or srow.get("season_name") or "").strip()
        digits = re.search(r"\d+", name)
        snum = (_int(srow.get("season_number"))
                or (int(digits.group()) if digits else 0) or index)
        nums = _numbers(srow.get("series"))
        if nums:
            cmd = str(srow.get("cmd") or "")
            return snum, name, [(_episode_key(sid, snum, n), n, {},
                                 {"cmd": cmd, "series": n}) for n in nums]
        season_id = str(srow.get("id") or "")
        eps: list[tuple[str, int, dict, dict]] = []
        rows = [r for r in self._drill(module, sid, season_id)
                if _is_episode(r)]
        for j, erow in enumerate(rows, 1):
            n = (_int(erow.get("series_number"))
                 or _int(erow.get("episode_number")) or j)
            raw_id = str(erow.get("id") or "")
            eid = raw_id if raw_id.isdigit() else _episode_key(sid, snum, n)
            cmd = str(erow.get("cmd") or "")
            rec = {"cmd": cmd} if cmd else {
                "module": module, "movie_id": sid, "season_id": season_id,
                "episode_id": raw_id}
            eps.append((eid, n, erow, rec))
        return snum, name, eps

    def _episode(self, eid: str, season: int, num: int, row: dict,
                 show: dict) -> dict:
        title = str(row.get("name") or "").strip()
        secs = _int(row.get("time")) * 60
        return {
            "id": eid, "episode_num": num, "title": title,
            "container_extension": _container(str(row.get("cmd")
                                                  or show.get("_cmd") or "")),
            "info": {"movie_image": self._image(row.get("screenshot_uri")),
                     "plot": str(row.get("description") or "").strip(),
                     "duration_secs": secs, "duration": _hms(secs),
                     "name": title},
            "custom_sid": "", "added": str(_utc_stamp(row.get("added")) or 0),
            "season": season, "direct_source": ""}

    # -- what plays a title, across sessions ----------------------------------

    def _known_locked(self) -> dict[str, dict]:
        """(under _known_lock) What playing a title needs, by "v<id>" (a
        film) and "e<id>" (an episode). Kept on disk: Continue watching
        plays an episode by its id alone, without opening the series."""
        if self._known is None:
            self._known = {}
            hit = self._disk_hit(self._KNOWN_KEY)
            for pair in hit[1] if hit else []:
                if (isinstance(pair, list) and len(pair) == 2
                        and isinstance(pair[1], dict)):
                    self._known[str(pair[0])] = pair[1]
        return self._known

    def _known_get(self, key: str) -> dict:
        with self._known_lock:
            return dict(self._known_locked().get(key) or {})

    def _remember(self, entries: dict[str, dict]) -> None:
        with self._known_lock:
            known = self._known_locked()
            fresh = {k: v for k, v in entries.items() if known.get(k) != v}
            if not fresh:
                return
            for k, v in fresh.items():
                known.pop(k, None)              # newest last
                known[k] = v
            while len(known) > self.KNOWN_MAX:
                known.pop(next(iter(known)))
            body = json.dumps([[k, v] for k, v in known.items()])
            self._save_list(self._KNOWN_KEY, time.time(),
                            body.encode("utf-8"))

    # -- playback -------------------------------------------------------------

    def _cached_link(self, key: str) -> str:
        with self._link_lock:
            hit = self._links.get(key)
        if hit and time.monotonic() - hit[0] < self.LINK_CACHE_SECS:
            return hit[1]
        return ""

    def _failed_lately(self, key: str) -> bool:
        with self._link_lock:
            t = self._failed.get(key)
        return t is not None and time.monotonic() - t < self.LINK_FAIL_SECS

    _LINK_KEYS = {"live_url": "l", "vod_url": "v", "episode_url": "e"}

    def link_ready(self, method: str, ident: int | str) -> bool:
        """Whether *method*(*ident*) answers at once: its link is kept, or
        the portal just gave none."""
        prefix = self._LINK_KEYS.get(method)
        if prefix is None:
            return True
        key = f"{prefix}{ident}"
        return bool(self._cached_link(key)) or self._failed_lately(key)

    def _link(self, key: str, kind: str, cmd: str, series: str = "",
              deadline: float | None = None) -> str:
        """A playable address for *cmd* from create_link, kept for
        LINK_CACHE_SECS under *key*. The cmd itself stands in when
        create_link fails and it is a real address; "" when neither."""
        hit = self._cached_link(key)
        if hit:
            return hit
        if self._failed_lately(key):
            return ""
        if not cmd:
            log.warning("stalker @ %s: nothing known to play %s",
                        self.server, key)
            return ""
        if deadline is None:
            deadline = time.monotonic() + self.LINK_BUDGET_SECS
        url = ""
        try:
            js = self._call({"type": kind, "action": "create_link",
                             "cmd": cmd, "series": series,
                             "forced_storage": "", "disable_ad": "0",
                             "download": "0", "force_ch_link_check": "0"},
                            read=self.LINK_BUDGET_SECS, deadline=deadline)
            got = js.get("cmd") if isinstance(js, dict) else js
            url = strip_cmd(got if isinstance(got, str) else "")
            if not _URL.match(url):
                err = js.get("error") if isinstance(js, dict) else ""
                log.warning("stalker create_link %s @ %s: no address%s", key,
                            self.server, f" ({err})" if err else "")
                url = ""
        except Exception as e:
            log.warning("stalker create_link %s @ %s failed - %s: %s", key,
                        self.server, type(e).__name__, self._redact(str(e)))
        if not url and _direct_url(strip_cmd(cmd)):
            url = strip_cmd(cmd)
        now = time.monotonic()
        with self._link_lock:
            if url:
                self._links = {k: v for k, v in self._links.items()
                               if now - v[0] < self.LINK_CACHE_SECS}
                self._links[key] = (now, url)
                self._failed.pop(key, None)
            else:
                self._failed = {k: t for k, t in self._failed.items()
                                if now - t < self.LINK_FAIL_SECS}
                self._failed[key] = now
        return url

    def forget_link(self, stream_id: int | str) -> None:
        """A channel's link is not to be served again: the stream it played
        ended or failed, and a portal's play token is often good once."""
        with self._link_lock:
            self._links.pop(f"l{stream_id}", None)
            self._failed.pop(f"l{stream_id}", None)

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        """A channel's address from create_link: the portal hands out
        short-lived links. This runs on the window's thread as a channel
        starts, so all of it - a new handshake included - keeps within
        LINK_BUDGET_SECS; "" when no link comes. *fmt* is the portal's to
        choose."""
        row = self._find(self._LIVE_KEYS, "stream_id", stream_id)
        if row is None and self._disk_hit(("itv_all",)) is not None:
            # The lineup is on disk but not in memory yet: a favourite
            # played before the channel list loaded.
            row = self._find(("itv_all",), "stream_id", stream_id)
        return self._link(f"l{stream_id}", "itv",
                          str((row or {}).get("_cmd") or ""))

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        key = f"v{stream_id}"
        hit = self._cached_link(key)
        if hit:
            return hit
        row = self._find(("vod",), "stream_id", stream_id)
        cmd = str((row or {}).get("_cmd") or "")
        if cmd:
            self._remember({key: {"cmd": cmd}})
        else:
            # A film resumed from an earlier session, or else the name
            # Ministra gives a film by its id.
            cmd = (str(self._known_get(key).get("cmd") or "")
                   or f"/media/{stream_id}.mpg")
        return self._link(key, "vod", cmd)

    def episode_url(self, episode_id: int | str,
                    ext: str | None = None) -> str:
        key = f"e{episode_id}"
        hit = self._cached_link(key)
        if hit:
            return hit
        rec = self._known_get(key)
        deadline = time.monotonic() + self.LINK_BUDGET_SECS
        cmd = str(rec.get("cmd") or "")
        if not cmd and rec.get("episode_id"):
            cmd = self._episode_file(rec, deadline)
        return self._link(key, "vod", cmd, str(rec.get("series") or ""),
                          deadline)

    def _episode_file(self, rec: dict, deadline: float) -> str:
        """The cmd of an episode's first file (Ministra 5 keeps seasons,
        episodes and files apart)."""
        try:
            js = self._call({"type": rec.get("module") or "vod",
                             "action": "get_ordered_list",
                             "movie_id": rec.get("movie_id"),
                             "season_id": rec.get("season_id"),
                             "episode_id": rec.get("episode_id"), "p": 1},
                            read=self.LINK_BUDGET_SECS, deadline=deadline)
        except Exception as e:
            log.warning("stalker episode files @ %s failed - %s: %s",
                        self.server, type(e).__name__, self._redact(str(e)))
            return ""
        rows = js.get("data") if isinstance(js, dict) else js
        for f in rows if isinstance(rows, list) else []:
            if isinstance(f, dict) and f.get("cmd"):
                return str(f["cmd"])
        return ""

    def timeshift_urls(self, stream_id: int | str, start_dt: datetime,
                       duration_min: int) -> list[str]:
        """Nothing: a portal's archive is played by programme, and asking
        for its link takes the network - archive_link, which the window
        calls off its thread (archive_by_programme)."""
        return []

    # -- programme guide ------------------------------------------------------

    def short_epg(self, stream_id: int | str, limit: int = 8) -> list[dict]:
        js = self._call({"type": "itv", "action": "get_short_epg",
                         "ch_id": str(stream_id), "size": str(limit)})
        rows = js.get("data") if isinstance(js, dict) else js
        return self._programmes(rows, stream_id)

    def epg_table(self, stream_id: int | str) -> list[dict]:
        """Today's programme table (get_simple_data_table, page by page),
        or short_epg's answer where the portal has no table."""
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return (self._programmes(self._table_rows(stream_id, date), stream_id)
                or self.short_epg(stream_id, 24))

    def _table_rows(self, stream_id: int | str, date: str) -> list[dict]:
        """A channel's guide rows on *date* (YYYY-MM-DD), as the portal
        gives them; none where it has no table."""
        try:
            return self._pages({"type": "epg",
                                "action": "get_simple_data_table",
                                "ch_id": str(stream_id), "date": date},
                               limit=self.EPG_PAGES)
        except requests.HTTPError as e:
            if not _not_offered(e):
                raise
        except ValueError:
            pass
        return []

    def _programmes(self, rows: Any, stream_id: int | str) -> list[dict]:
        """A portal's programmes as Xtream's epg_listings: title and
        description base64 (what b64() decodes), times as unix stamps."""
        channel = str((self._find(self._LIVE_KEYS, "stream_id", stream_id)
                       or {}).get("epg_channel_id") or stream_id)
        by_start: dict[int, dict] = {}
        for p in rows if isinstance(rows, list) else []:
            if not isinstance(p, dict):
                continue
            start = (_int(p.get("start_timestamp"))
                     or _utc_stamp(p.get("time")) or 0)
            if start <= 0:
                continue
            stop = (_int(p.get("stop_timestamp"))
                    or _utc_stamp(p.get("time_to"))
                    or start + _int(p.get("duration")))
            pid = str(p.get("id") or "")
            by_start.setdefault(start, {
                "id": pid, "epg_id": pid,
                "title": _b64(str(p.get("name") or "").strip()),
                "lang": "", "start": _local(start), "end": _local(stop),
                "description": _b64(str(p.get("descr") or "").strip()),
                "channel_id": channel, "start_timestamp": str(start),
                "stop_timestamp": str(stop)})
        return [by_start[k] for k in sorted(by_start)]

    # -- archive (catch-up) ---------------------------------------------------

    def _day_rows(self, stream_id: int | str, day: str) -> list[dict]:
        """(worker thread) _table_rows, kept a few minutes: a scrub, the
        next programme and the archive's list read the same days."""
        key = (str(stream_id), day)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        keep = (self.ARCHIVE_PAST_SECS if day < today
                else self.ARCHIVE_TODAY_SECS)
        with self._link_lock:
            hit = self._days.get(key)
        if hit and time.monotonic() - hit[0] < keep:
            return hit[1]
        rows = self._table_rows(stream_id, day)
        now = time.monotonic()
        with self._link_lock:
            self._days = {k: v for k, v in self._days.items()
                          if now - v[0] < self.ARCHIVE_PAST_SECS}
            self._days[key] = (now, rows)
        return rows

    @staticmethod
    def _days_around(stamp: float) -> list[str]:
        """The guide days a programme on at *stamp* may be listed under:
        its own UTC day, the day before (it began before midnight), the day
        after (a portal that dates its days in its own time zone)."""
        day = datetime.fromtimestamp(stamp, timezone.utc).date()
        return [str(day + timedelta(days=d)) for d in (0, -1, 1)]

    def archive_programme(self, stream_id: int | str, start: float,
                          stop: float | None = None) -> dict | None:
        """(worker thread) The programme in the portal's guide to play from
        *start* (pick_programme): {"start", "stop", "row"}, or None when
        the guide has none then."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for day in self._days_around(start):
            if day > today:
                break
            found = pick_programme(
                _archive_rows(self._day_rows(stream_id, day)), start, stop)
            if found is not None:
                return found
        return None

    def archive_link(self, stream_id: int | str, start: float,
                     stop: float | None = None) -> dict | None:
        """(worker thread) A channel's archive from *start* (unix time):
        the programme then in the portal's guide - with *stop*, the one a
        guide's programme from *start* to *stop* stands for - and its link
        from create_link (type=tv_archive). The link begins at the
        programme's own start, so *offset* is how far into it *start* is:

            {"url", "start", "stop", "offset", "title", "id"}

        None when the portal has no such programme or gives no address for
        it. A network failure raises: it says nothing about the archive."""
        try:
            prog = self.archive_programme(stream_id, start, stop)
        except LocalizedError as e:
            log.warning("stalker tv_archive @ %s: no guide - %s", self.server,
                        e)
            return None
        if prog is None:
            log.info("stalker tv_archive @ %s: channel %s has no programme "
                     "in the guide at %s", self.server, for_log(stream_id),
                     for_log(_local(int(start))))
            return None
        row = prog["row"]
        pid = _text(row.get("id"))
        if "mark_archive" in row and not _flag(row.get("mark_archive")):
            # Asked all the same: a programme still on air is not marked
            # yet, and some portals never mark one.
            log.debug("stalker tv_archive @ %s: programme %s is not marked "
                      "as in the archive", self.server, for_log(pid))
        url = self._archive_create_link(archive_cmds(row))
        if not url:
            return None
        return {"url": url, "start": prog["start"], "stop": prog["stop"],
                "offset": max(0.0, float(start) - prog["start"]),
                "title": _text(row.get("name")), "id": pid}

    def _archive_create_link(self, cmds: list[str]) -> str:
        """The first address create_link gives for one of *cmds*, or "".
        Raises a network failure when that is all the portal gave."""
        deadline = time.monotonic() + self.ARCHIVE_BUDGET_SECS
        down: Exception | None = None
        for cmd in cmds:
            try:
                js = self._call({"type": "tv_archive", "action": "create_link",
                                 "cmd": cmd, "series": "",
                                 "forced_storage": "", "disable_ad": "0",
                                 "download": "0", "force_ch_link_check": "0"},
                                read=self.ARCHIVE_BUDGET_SECS,
                                deadline=deadline)
            except (requests.Timeout, requests.ConnectionError) as e:
                down = e
                break               # the portal is not answering at all
            except Exception as e:
                log.warning("stalker tv_archive create_link @ %s failed - "
                            "%s: %s", self.server, type(e).__name__,
                            self._redact(str(e)))
                continue
            url = archive_address(js)
            if url:
                log.info("stalker tv_archive @ %s: a link for %s",
                         self.server, for_log(cmd))
                return url
            err = js.get("error") if isinstance(js, dict) else ""
            log.info("stalker tv_archive create_link @ %s: no address for "
                     "%s%s", self.server, for_log(cmd), f" ({err})" if err else "")
        if down is not None:
            raise down
        return ""

    def archive_table(self, stream_id: int | str, days: int) -> list[dict]:
        """(worker thread) The channel's programmes of the last *days* days
        and today, as epg_table has them, for the archive's list: those the
        portal marks as in the archive, where it marks any."""
        days = max(0, min(int(days), self.ARCHIVE_TABLE_DAYS))
        today = datetime.now(timezone.utc).date()
        rows: list[dict] = []
        for d in range(days + 1):
            day = str(today - timedelta(days=d))
            try:
                rows.extend(self._day_rows(stream_id, day))
            except Exception as e:
                if d == 0:
                    raise
                # The days already read are worth showing.
                log.info("stalker archive list @ %s: stopped at %s - %s: %s",
                         self.server, for_log(day), type(e).__name__,
                         self._redact(str(e)))
                break
        if any(_flag(r.get("mark_archive")) for r in rows):
            rows = [r for r in rows if "mark_archive" not in r
                    or _flag(r.get("mark_archive"))]
        return self._programmes(rows, stream_id) or self.epg_table(stream_id)

    def xmltv(self) -> bytes:
        """Nothing: portals rarely serve XMLTV (see epg_url)."""
        return b""
