"""Plex Media Server, signed in the way Plex's own apps are.

Signing in is Plex's PIN flow (``create_pin``, ``auth_url``, ``check_pin``):
the app asks plex.tv for a PIN under its own client identifier (this
install's device id), the browser opens app.plex.tv where the person signs
in, and the PIN then carries a token. With it plex.tv lists the person's
servers (``servers``) and how each is reached: on the home network first,
then from outside, Plex's relay last (``pick_connection`` keeps the first
that answers). The app pays nothing and asks nothing of the person beyond
what Plex itself does.

Movie and show libraries are the films and series, with the server's
artwork; a title plays as its file (the part's own address with the token)
or through the server's HLS transcoder where the source asks for a
bitrate; what plays is told to the server's timeline, so its resume point
and watched mark follow. Live TV and its guide come from the server's DVR
when it has one - that needs a tuner and Plex Pass on the server owner's
side, and without them the source simply has no channels (``live_note``
says why). Servers on the home network answer GDM (UDP 32414).
"""

from __future__ import annotations

import socket
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit

from .. import VERSION
from ..core._lazy_requests import requests
from ..core.log import log, register_secrets
from ..i18n import LocalizedError, tr
from .boxes import USER_AGENT, build_xmltv, items, normalize_server, rows_of
from .mediaserver import PRODUCT, MediaServerClient, device_id, to_int

PLEX_TV = "https://plex.tv"
APP_AUTH = "https://app.plex.tv/auth#?"
DEFAULT_PORT = 32400
GDM_PORT = 32414
GDM_GROUP = "239.0.0.250"
GDM_SEARCH = b"M-SEARCH * HTTP/1.1\r\n\r\n"
PAGE = 1000
MAX_ITEMS = 50_000
LIBRARY = "com.plexapp.plugins.library"


def _platform() -> str:
    return ("Linux" if sys.platform.startswith("linux") else "macOS" if sys.platform == "darwin"
            else "Windows" if sys.platform.startswith("win") else sys.platform)


def plex_headers(token: str = "") -> dict[str, str]:
    """What Plex knows the app by: its name, version and this install's
    client identifier - never the computer's own name."""
    out = {"X-Plex-Product": PRODUCT, "X-Plex-Version": VERSION,
           "X-Plex-Client-Identifier": device_id(), "X-Plex-Platform": _platform(),
           "X-Plex-Device": _platform(), "X-Plex-Device-Name": f"{PRODUCT} ({_platform()})",
           "X-Plex-Provides": "player", "Accept": "application/json"}
    if token:
        out["X-Plex-Token"] = token
    return out


def _session(token: str = "", server: str = "") -> Any:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers.update(plex_headers(token))
    from ..core import nerd
    nerd.current().apply_session(s, {"_kind": "plex", "_server": server})
    return s


def _get(url: str, token: str = "", params: dict | None = None, method: str = "GET",
         timeout: tuple[float, float] = (6.0, 20.0)) -> Any:
    with _session(token) as s:
        try:
            return s.request(method, url, params=params, timeout=timeout)
        except requests.RequestException:
            raise LocalizedError("err_ms_plex_unreachable") from None


def _js(r: Any) -> Any:
    try:
        return r.json()
    except ValueError:
        raise LocalizedError("err_unexpected_response") from None


# -- signing in (plex.tv) ------------------------------------------------------

def create_pin() -> dict:
    """A new PIN: {"id", "code"}. The code goes to app.plex.tv (auth_url)."""
    r = _get(PLEX_TV + "/api/v2/pins", params={"strong": "true"}, method="POST")
    r.raise_for_status()
    js = _js(r)
    if not isinstance(js, dict) or not js.get("id") or not js.get("code"):
        raise LocalizedError("err_unexpected_response")
    return {"id": int(js["id"]), "code": str(js["code"])}


def auth_url(code: str) -> str:
    """Where the person signs in to Plex for this PIN, in their browser."""
    return APP_AUTH + urlencode({"clientID": device_id(), "code": code,
                                 "context[device][product]": PRODUCT})


def check_pin(pin_id: int, code: str) -> str:
    """The token once the person has signed in, "" until then. Raises
    LocalizedError for a PIN that is gone (expired)."""
    r = _get(f"{PLEX_TV}/api/v2/pins/{int(pin_id)}", params={"code": code})
    if r.status_code in (404, 410):
        raise LocalizedError("err_ms_pin_expired")
    r.raise_for_status()
    js = _js(r)
    token = str(js.get("authToken") or "") if isinstance(js, dict) else ""
    register_secrets(token)
    return token


def account_name(token: str) -> str:
    r = _get(PLEX_TV + "/api/v2/user", token)
    if r.status_code == 401:
        raise LocalizedError("err_ms_signed_out", kind="Plex")
    r.raise_for_status()
    js = _js(r)
    if not isinstance(js, dict):
        return ""
    return str(js.get("username") or js.get("title") or js.get("email") or "")


def _connection_order(res: dict) -> list[str]:
    """A server's addresses, best first: the home network (its plain
    http:// too, for a network that blocks plex.direct names), then from
    outside, Plex's relay last; IPv6 after IPv4 in each."""
    groups: list[list[tuple[bool, str]]] = [[], [], []]
    for c in rows_of(res.get("connections")):
        if not c.get("uri"):
            continue
        uri = str(c["uri"]).rstrip("/")
        v6 = bool(c.get("IPv6"))
        rank = 2 if c.get("relay") else 0 if c.get("local") else 1
        groups[rank].append((v6, uri))
        addr, port = str(c.get("address") or ""), to_int(c.get("port")) or DEFAULT_PORT
        if rank == 0 and not v6 and addr and str(c.get("protocol") or "") == "https":
            groups[rank].append((v6, f"http://{addr}:{port}"))
    out: list[str] = []
    for group in groups:
        for _v6, uri in sorted(group, key=lambda x: x[0]):
            if uri not in out:
                out.append(uri)
    return out


def servers(token: str) -> list[dict]:
    """The person's servers: {"name", "id", "token", "owned",
    "connections"} (connections best first), their own first."""
    r = _get(PLEX_TV + "/api/v2/resources", token,
             params={"includeHttps": "1", "includeRelay": "1", "includeIPv6": "1"})
    if r.status_code == 401:
        raise LocalizedError("err_ms_signed_out", kind="Plex")
    r.raise_for_status()
    js = _js(r)
    out: list[dict[str, Any]] = []
    for res in js if isinstance(js, list) else []:
        if not isinstance(res, dict) or "server" not in str(res.get("provides") or ""):
            continue
        access = str(res.get("accessToken") or token)
        register_secrets(access)
        out.append({"name": str(res.get("name") or "Plex"), "id": str(res.get("clientIdentifier") or ""),
                    "token": access, "owned": bool(res.get("owned")),
                    "connections": _connection_order(res)})
    out.sort(key=lambda s: (not s["owned"], str(s["name"]).lower()))
    return out


def identity(uri: str, timeout: float = 4.0) -> str:
    """The machine identifier of the server at *uri*, "" when none answers."""
    try:
        with _session(server=uri) as s:
            r = s.get(uri.rstrip("/") + "/identity", timeout=(timeout, timeout))
        if not r.ok:
            return ""
        js = r.json()
    except Exception:
        return ""
    mc = js.get("MediaContainer") if isinstance(js, dict) else None
    return str((mc or {}).get("machineIdentifier") or "") if isinstance(mc, dict) else ""


def pick_connection(connections: list[str], server_id: str = "", timeout: float = 4.0) -> str:
    """The best of *connections* that answers as this server, all asked at
    once; "" when none does."""
    if not connections:
        return ""
    with ThreadPoolExecutor(max_workers=min(8, len(connections))) as pool:
        got = list(pool.map(lambda u: identity(u, timeout), connections))
    for uri, ident in zip(connections, got, strict=True):
        if ident and (not server_id or ident == server_id):
            return uri
    return ""


def _tags(m: dict, name: str, limit: int = 12) -> str:
    return ", ".join(str(t.get("tag")) for t in items(m.get(name))[:limit]
                     if isinstance(t, dict) and t.get("tag"))


def _tmdb(m: dict) -> int | None:
    for g in items(m.get("Guid")):
        gid = str(g.get("id") or "") if isinstance(g, dict) else ""
        if gid.startswith("tmdb://"):
            return to_int(gid[7:])
    return None


def _major(number: Any) -> int:
    try:
        return int(str(number).split(".")[0].split("-")[0].strip())
    except ValueError:
        return 0


#: Most nodes of a tune answer looked through for its session.
SESSION_NODES = 100_000


def _find_session(node: Any) -> str:
    """The live session's key somewhere in a tune answer: depth first in
    the answer's own order, as before, but without recursion (a document
    nested thousands deep, which Python 3.12's json reads, went over the
    recursion limit) and through at most SESSION_NODES nodes."""
    stack: list[Any] = [node]
    seen = 0
    while stack and seen < SESSION_NODES:
        cur = stack.pop()
        seen += 1
        if isinstance(cur, dict):
            later: list[Any] = []
            for k, v in cur.items():
                if k == "key" and isinstance(v, str) and v.startswith("/livetv/sessions/"):
                    return v
                if isinstance(v, (dict, list)):
                    later.append(v)
            stack.extend(reversed(later))
        elif isinstance(cur, list):
            stack.extend(reversed([v for v in cur if isinstance(v, (dict, list))]))
    return ""


class PlexClient(MediaServerClient):
    LOG_NAME = "plex"
    KIND = "Plex"
    DEFAULT_PORT = DEFAULT_PORT
    GUIDE_HOURS = 48
    # A channel's own address is the tune request: nothing to hand out.
    LIVE_FILE = False

    def __init__(self, server: str, token: str, server_id: str = "", account: str = "",
                 options: dict | None = None) -> None:
        super().__init__(normalize_server(server, DEFAULT_PORT), token, server_id, account, options)
        self.server_id = (server_id or "").strip()
        self.session.headers.update(plex_headers(self.token))
        self.connections = [c for c in str(self.opt("connections", "") or "").split() if c]

    # -- requests ---------------------------------------------------------------

    def mc(self, path: str, params: dict | None = None,
           timeout: tuple[float, float] | None = None, method: str = "GET") -> dict:
        """A MediaContainer from the server ({} when the answer has none)."""
        js = self.json(path, params, timeout, method=method)
        mc = js.get("MediaContainer") if isinstance(js, dict) else None
        return mc if isinstance(mc, dict) else {}

    def _metadata(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        start = 0
        while len(out) < MAX_ITEMS:
            mc = self.mc(path, dict(params, **{"X-Plex-Container-Start": str(start),
                                               "X-Plex-Container-Size": str(PAGE)}),
                         timeout=self.LIST_TIMEOUT)
            rows = rows_of(mc.get("Metadata"))
            out += rows
            total = to_int(mc.get("totalSize"))
            if len(rows) < PAGE or (total and len(out) >= total):
                break
            start += len(rows)
        return out

    def _probe(self) -> dict:
        try:
            root = self.mc("/", timeout=(4.0, 15.0))
        except LocalizedError:
            raise
        except requests.RequestException:
            # The address it had is gone (a new IP, away from home): the next
            # of the server's own that answers.
            others = [c for c in self.connections if c.rstrip("/") != self.server]
            found = pick_connection(others, self.server_id)
            if not found:
                raise
            log.info("plex: the server answers at another of its addresses now")
            self.server = found.rstrip("/")
            root = self.mc("/", timeout=(4.0, 15.0))
        ident = str(root.get("machineIdentifier") or "")
        if not ident:
            raise ValueError("not a Plex Media Server")
        version = str(root.get("version") or "")
        name = str(root.get("friendlyName") or "Plex")
        return {"kind": "plex", "name": f"{name} (Plex {version.split('-')[0]})".replace(" )", ")"),
                "version": version, "server_id": ident, "user": self.account}

    # -- addresses --------------------------------------------------------------

    def image(self, path: Any, width: int = 320, height: int = 480) -> str:
        p = str(path or "")
        if not p:
            return ""
        if p.startswith("http"):
            return p
        return self.url("/photo/:/transcode") + "?" + urlencode(
            {"width": str(width), "height": str(height), "minSize": "1", "upscale": "1",
             "url": p, "X-Plex-Token": self.token})

    def _kbps(self) -> int:
        try:
            mbps = float(str(self.opt("ms_quality", "") or "0"))
        except ValueError:
            mbps = 0.0
        return int(mbps * 1000) if mbps > 0 else 0

    def transcode_url(self, path: str, kbps: int = 0, live: bool = False) -> str:
        session = uuid.uuid4().hex
        params = {"path": path, "mediaIndex": "0", "partIndex": "0", "protocol": "hls",
                  "fastSeek": "1", "directPlay": "0", "directStream": "1", "directStreamAudio": "1",
                  "videoQuality": "100", "videoResolution": "1920x1080", "copyts": "1",
                  "session": session, "X-Plex-Session-Identifier": session,
                  "X-Plex-Client-Identifier": self.device, "X-Plex-Product": PRODUCT,
                  "X-Plex-Platform": "Generic", "X-Plex-Token": self.token}
        if kbps:
            params["maxVideoBitrate"] = str(kbps)
        if live:
            params["live"] = "1"
        return self.url("/video/:/transcode/universal/start.m3u8") + "?" + urlencode(params)

    def item_url(self, rk: str, m: dict) -> tuple[str, str]:
        """(address, container) of a film or an episode. Not "play_url":
        the window asks a box that transcodes by that name (ui/mw_box.py),
        and a Plex channel then never played."""
        media = next(iter(rows_of(m.get("Media"))), {})
        part = next(iter(rows_of(media.get("Part"))), {})
        ext = str(part.get("container") or media.get("container") or "mkv")
        kbps = self._kbps()
        key = str(part.get("key") or "")
        if kbps or not key:
            return self.transcode_url(f"/library/metadata/{rk}", kbps), "ts"
        return self.url(key) + "?" + urlencode({"X-Plex-Token": self.token}), ext

    def item_of_url(self, url: str) -> str:
        path = ""
        try:
            from urllib.parse import parse_qs
            path = (parse_qs(urlsplit(url or "").query).get("path") or [""])[0]
        except ValueError:
            pass
        if path.startswith("/library/metadata/"):
            return path.rsplit("/", 1)[-1]
        return ""

    # -- what the server has ----------------------------------------------------

    @staticmethod
    def _watch(m: dict) -> dict:
        return {"played": bool(to_int(m.get("viewCount"))),
                "pos": (to_int(m.get("viewOffset")) or 0) / 1000,
                "last_played": to_int(m.get("lastViewedAt")) or 0}

    def _film(self, m: dict, library: str) -> dict:
        rk = str(m.get("ratingKey") or "")
        url, ext = self.item_url(rk, m)
        art = self.image(m.get("art"), 1280, 720)
        return {"key": rk, "title": m.get("title"), "category": library, "url": url,
                "icon": self.image(m.get("thumb")), "backdrop": [art] if art else [],
                "added": to_int(m.get("addedAt")) or 0, "ext": ext, "plot": m.get("summary"),
                "genre": _tags(m, "Genre"), "director": _tags(m, "Director"), "cast": _tags(m, "Role"),
                "year": m.get("year"), "released": str(m.get("originallyAvailableAt") or "")[:10],
                "secs": (to_int(m.get("duration")) or 0) / 1000,
                "rating": m.get("audienceRating") or m.get("rating"),
                "official": m.get("contentRating"), "tmdb": _tmdb(m), **self._watch(m)}

    def _series(self, m: dict, library: str) -> dict:
        art = self.image(m.get("art"), 1280, 720)
        leaves, seen = to_int(m.get("leafCount")) or 0, to_int(m.get("viewedLeafCount")) or 0
        return {"key": str(m.get("ratingKey") or ""), "title": m.get("title"), "category": library,
                "icon": self.image(m.get("thumb")), "backdrop": [art] if art else [],
                "added": to_int(m.get("addedAt")) or 0, "updated": to_int(m.get("updatedAt")) or 0,
                "plot": m.get("summary"), "genre": _tags(m, "Genre"), "cast": _tags(m, "Role"),
                "year": m.get("year"), "released": str(m.get("originallyAvailableAt") or "")[:10],
                "rating": m.get("audienceRating") or m.get("rating"),
                "played": bool(leaves) and seen >= leaves, "tmdb": _tmdb(m)}

    def _episode(self, m: dict) -> dict:
        rk = str(m.get("ratingKey") or "")
        url, ext = self.item_url(rk, m)
        return {"key": rk, "title": m.get("title"), "season": m.get("parentIndex") or 0,
                "episode": m.get("index") or 0, "url": url, "ext": ext,
                "icon": self.image(m.get("thumb"), 480, 270) or self.image(m.get("grandparentThumb")),
                "plot": m.get("summary"), "secs": (to_int(m.get("duration")) or 0) / 1000,
                "released": str(m.get("originallyAvailableAt") or "")[:10],
                "added": to_int(m.get("addedAt")) or 0,
                "series_key": str(m.get("grandparentRatingKey") or ""), **self._watch(m)}

    def _catalogue(self) -> dict:
        sections = rows_of(self.mc("/library/sections").get("Directory"))
        movie_libs = [(str(d["key"]), str(d.get("title") or "")) for d in sections
                      if d.get("type") == "movie" and d.get("key")]
        show_libs = [(str(d["key"]), str(d.get("title") or "")) for d in sections
                     if d.get("type") == "show" and d.get("key")]
        movies = [self._film(m, lib) for lib, _n in movie_libs
                  for m in self._metadata(f"/library/sections/{quote(lib)}/all",
                                          {"type": "1", "includeGuids": "1"}) if m.get("ratingKey")]
        series = [self._series(m, lib) for lib, _n in show_libs
                  for m in self._metadata(f"/library/sections/{quote(lib)}/all",
                                          {"type": "2", "includeGuids": "1"}) if m.get("ratingKey")]
        channels, self.live_note = self._live()
        return {"movies": movies, "movie_libraries": movie_libs, "series": series,
                "series_libraries": show_libs, "channels": channels, "recordings": [],
                "resume": self._resume_list()}

    def _resume_list(self) -> list[dict]:
        try:
            rows = rows_of(self.mc("/library/onDeck").get("Metadata"))
        except LocalizedError:
            raise
        except Exception as e:
            log.info("plex: no on-deck list (%s)", type(e).__name__)
            return []
        out = []
        for m in rows:
            if not isinstance(m, dict) or not m.get("ratingKey") or not to_int(m.get("viewOffset")):
                continue
            if m.get("type") == "episode":
                out.append(self._episode(m))
            elif m.get("type") == "movie":
                out.append(self._film(m, ""))
        return out

    def _episodes_of(self, series_key: str) -> list[dict]:
        rows = self._metadata(f"/library/metadata/{quote(series_key)}/allLeaves", {"includeGuids": "1"})
        return [self._episode(m) for m in rows if m.get("ratingKey")]

    # -- live TV & DVR (the server owner's Plex Pass) ---------------------------

    def _live(self) -> tuple[list[dict], str]:
        try:
            dvrs = rows_of(self.mc("/livetv/dvrs").get("Dvr"))
        except LocalizedError:
            raise
        except Exception as e:
            log.info("plex: no live TV (%s)", type(e).__name__)
            dvrs = []
        if not dvrs:
            return [], tr("ms_plex_no_live")
        everything = tr("box_all_channels")
        out: list[dict] = []
        for d in dvrs:
            dkey = str(d.get("key") or "")
            rows: list[dict] = []
            lineup = str(d.get("lineup") or "")
            if lineup:
                try:
                    rows = rows_of(self.mc("/livetv/epg/channels", {"lineup": lineup}).get("Channel"))
                except LocalizedError:
                    raise
                except Exception as e:
                    log.info("plex: no channel list for a DVR (%s)", type(e).__name__)
            if not rows:
                for dev in rows_of(d.get("Device")):
                    for cm in rows_of(dev.get("ChannelMapping")):
                        if str(cm.get("enabled", "1")) in ("1", "true") and cm.get("channelKey"):
                            rows.append({"identifier": cm["channelKey"], "title": cm.get("channelKey")})
            for ch in rows:
                ident = str(ch.get("identifier") or ch.get("key") or "")
                if not dkey or not ident:
                    continue
                number = ch.get("channelVcn") or ch.get("vcn") or ""
                out.append({"key": f"{dkey}/{ident}",
                            "name": ch.get("title") or ch.get("callSign") or str(number) or ident,
                            "category": everything,
                            "url": self.url(f"/livetv/dvrs/{quote(dkey)}/channels/{quote(ident)}/tune"),
                            "icon": self.image(ch.get("thumb"), 160, 160), "epg_id": ident,
                            "num": _major(number), "_open": True})
        if not out:
            return [], tr("ms_plex_no_live")
        return out, ""

    def _live_open(self, row: dict) -> str:
        dkey, _, ident = str(row["_key"]).partition("/")
        js = self.json(f"/livetv/dvrs/{quote(dkey)}/channels/{quote(ident)}/tune",
                       {"autoPreview": "1"}, method="POST", timeout=(6.0, 30.0))
        key = _find_session(js)
        if not key:
            raise LocalizedError("err_ms_no_stream")
        return self.transcode_url(key, self._kbps(), live=True)

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        channels = self.guide_channels()
        if not channels:
            return b""
        providers = items(self.mc("/media/providers").get("MediaProvider"))
        epg = next((str(p.get("identifier")) for p in providers if isinstance(p, dict)
                    and str(p.get("identifier") or "").startswith("tv.plex.providers.epg")), "")
        if not epg:
            return b""
        now = int(time.time())
        query = f"type=1,4&sort=beginsAt&endsAt>={now - 3600}&beginsAt<={now + self.GUIDE_HOURS * 3600}"
        events: list[dict] = []
        start = 0
        while start < 100_000:
            mc = self.mc(f"/{epg}/grid?{query}", {"X-Plex-Container-Start": str(start),
                                                 "X-Plex-Container-Size": "2000"}, timeout=(6.0, 120.0))
            rows = rows_of(mc.get("Metadata"))
            for p in rows:
                show = str(p.get("grandparentTitle") or "")
                title = str(p.get("title") or "")
                for media in rows_of(p.get("Media")):
                    events.append({"channel": str(media.get("channelIdentifier") or ""),
                                   "start": to_int(media.get("beginsAt")) or 0,
                                   "stop": to_int(media.get("endsAt")) or 0,
                                   "title": show or title, "subtitle": title if show else "",
                                   "desc": str(p.get("summary") or ""), "category": _tags(p, "Genre", 3)})
            start += len(rows)
            total = to_int(mc.get("totalSize"))
            if progress and total:
                progress(min(99, start * 100 // total))
            if len(rows) < 2000 or (total and start >= total):
                break
        wanted = {cid for cid, _ in channels}
        events = [e for e in events if e["channel"] in wanted]
        log.info("plex: %d programme(s) for %d channel(s)", len(events), len(wanted))
        return build_xmltv(channels, events) if events else b""

    # -- what is played, back to the server -----------------------------------

    def _send(self, job: tuple) -> None:
        if job[0] == "mark":
            _, played, target = job
            self.request("GET", "/:/scrobble" if played else "/:/unscrobble",
                         params={"identifier": LIBRARY, "key": str(target["id"])})
            return
        _, event, target, pos, dur, paused = job
        session = str(target.get("session") or "")
        if target.get("kind") == "live":
            if event == "stop" and session:
                self.request("GET", "/video/:/transcode/universal/stop", params={"session": session})
            return
        rk = str(target["id"])
        state = {"start": "playing", "progress": "paused" if paused else "playing",
                 "stop": "stopped"}[event]
        self.request("GET", "/:/timeline", params={
            "ratingKey": rk, "key": f"/library/metadata/{rk}", "identifier": LIBRARY, "state": state,
            "time": str(int(pos * 1000)), "duration": str(int(dur * 1000))},
            headers={"X-Plex-Session-Identifier": session} if session else None)
        if event == "stop" and target.get("hls") and session:
            self.request("GET", "/video/:/transcode/universal/stop", params={"session": session})


# -- discovery on the local network (GDM, UDP 32414) ---------------------------

def parse_gdm(data: bytes, ip: str) -> dict | None:
    """A GDM answer from a Plex Media Server as {"kind", "server", "name",
    "id"}, or None for anything else."""
    text = data.decode("utf-8", "replace")
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or not lines[0].startswith("HTTP/") or " 200" not in lines[0]:
        return None
    fields = {}
    for line in lines[1:]:
        k, sep, v = line.partition(":")
        if sep:
            fields[k.strip().lower()] = v.strip()
    if "plex/media-server" not in fields.get("content-type", "").lower():
        return None
    port = to_int(fields.get("port")) or DEFAULT_PORT
    return {"kind": "plex", "server": f"http://{ip}:{port}", "id": fields.get("resource-identifier", ""),
            "name": f"{fields.get('name') or 'Plex'} - {ip}"}


def discover(timeout: float = 1.5, targets: tuple[tuple[str, int], ...] = (
        (GDM_GROUP, GDM_PORT), ("255.255.255.255", GDM_PORT))) -> list[dict]:
    """Every Plex Media Server that answers GDM here."""
    found: dict[str, dict] = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return []
    with s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            pass
        for t in targets:
            try:
                s.sendto(GDM_SEARCH, t)
            except OSError as e:
                log.debug("plex: GDM to %s: %s", t[0], e)
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            s.settimeout(left)
            try:
                data, (ip, _port) = s.recvfrom(4096)
            except (socket.timeout, TimeoutError):
                break
            except OSError as e:
                log.debug("plex: GDM: %s", e)
                break
            got = parse_gdm(data, ip)
            if got:
                found.setdefault(got["id"] or got["server"], got)
    return list(found.values())
