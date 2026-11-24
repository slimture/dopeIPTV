"""Jellyfin and Emby servers: one client, two flavours.

Emby is where Jellyfin came from and the API is the same where this app
needs it; what differs is the path prefix (Emby's under ``/emby``), the
headers the sign-in rides in (Jellyfin: ``Authorization: MediaBrowser ...,
Token=...``; Emby: ``X-Emby-Authorization`` and ``X-Emby-Token``), and
Quick Connect, which only Jellyfin has.

Signing in (``sign_in``, ``quick_connect_*``) leaves an access token and the
user's id; the password is sent once and never kept. Movie and TV-show
libraries are the films and series, Live TV channels the channels with the
server's guide, its DVR recordings films. A film or an episode plays as the
file itself (``/Videos/<id>/stream?static=true``) or, where a source says so
or the user's remote bitrate limit asks it, as the server's HLS
(``master.m3u8``, H.264/AAC). A live channel is opened first
(``PlaybackInfo``), as the official apps do. What plays is reported back
(``/Sessions/Playing``...), so the server keeps the resume point and the
watched mark. Servers on the home network answer a broadcast on UDP 7359.
"""

from __future__ import annotations

import json
import re
import select
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .. import VERSION
from ..core._lazy_requests import requests
from ..core.log import log, register_secrets
from ..core.nerd import is_home_host
from ..i18n import LocalizedError, tr
from .boxes import USER_AGENT, build_xmltv, items, normalize_server, rows_of
from .mediaserver import (
    PRODUCT, MediaServerClient, as_dict, device_id, iso_time, ticks, to_int,
)

DEFAULT_PORT = 8096
DISCOVERY_PORT = 7359
QUESTIONS = {"jellyfin": b"who is JellyfinServer?", "emby": b"who is EmbyServer?"}
FLAVORS = ("jellyfin", "emby")
NAMES = {"jellyfin": "Jellyfin", "emby": "Emby"}
PAGE = 500
MAX_ITEMS = 50_000
MOVIE_FIELDS = "Overview,Genres,ProviderIds,DateCreated"
SERIES_FIELDS = "Overview,Genres,ProviderIds,DateCreated,DateLastMediaAdded"
EPISODE_FIELDS = "Overview,DateCreated"
# What the app's player takes: anything (mpv), and for a server that will
# not hand a stream over as it is, H.264/AAC over HLS.
DEVICE_PROFILE: dict[str, Any] = {
    "MaxStreamingBitrate": 140_000_000,
    "DirectPlayProfiles": [{"Type": "Video"}, {"Type": "Audio"}],
    "TranscodingProfiles": [{"Container": "ts", "Type": "Video", "VideoCodec": "h264",
                             "AudioCodec": "aac,mp3,ac3", "Protocol": "hls", "Context": "Streaming",
                             "MaxAudioChannels": "6", "BreakOnNonKeyFrames": True}],
    "SubtitleProfiles": [{"Format": f, "Method": "Embed"}
                         for f in ("srt", "subrip", "ass", "ssa", "vtt", "pgs", "pgssub", "dvdsub")],
}
_VIDEO_ID = re.compile(r"/videos/([0-9a-f]{32})/", re.I)


def device_name() -> str:
    """What the server's device list calls this app: never the computer's
    own name."""
    plat = ("Linux" if sys.platform.startswith("linux") else "macOS" if sys.platform == "darwin"
            else "Windows" if sys.platform.startswith("win") else sys.platform)
    return f"{PRODUCT} ({plat})"


def auth_headers(flavor: str, device: str, token: str = "") -> dict[str, str]:
    """The headers a request is signed in with, as each flavour reads them."""
    fields = (f'Client="{PRODUCT}", Device="{device_name()}", DeviceId="{device}", '
              f'Version="{VERSION}"')
    if flavor == "emby":
        out = {"X-Emby-Authorization": f"Emby {fields}"}
        if token:
            out["X-Emby-Token"] = token
        return out
    return {"Authorization": f"MediaBrowser {fields}" + (f', Token="{token}"' if token else "")}


def server_url(text: str, flavor: str) -> str:
    """The address as stored: what was typed, the usual port on a bare host,
    without Emby's own prefix (the client adds it)."""
    s = normalize_server(text, DEFAULT_PORT)
    if flavor == "emby" and s.lower().endswith("/emby"):
        s = s[:-5]
    return s


def _prefix(flavor: str) -> str:
    return "/emby" if flavor == "emby" else ""


def _session(flavor: str, server: str = "") -> Any:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Accept"] = "application/json"
    s.headers.update(auth_headers(flavor, device_id()))
    from ..core import nerd
    nerd.current().apply_session(s, {"_kind": flavor, "_server": server})
    return s


def _call(flavor: str, server: str, method: str, path: str, body: Any = None,
          params: dict | None = None, timeout: tuple[float, float] = (6.0, 30.0)) -> Any:
    base = server_url(server, flavor)
    if not base:
        raise LocalizedError("err_box_not_found", kind=NAMES[flavor])
    with _session(flavor, base) as s:
        try:
            r = s.request(method, base + _prefix(flavor) + path, json=body, params=params,
                          timeout=timeout)
        except requests.RequestException:
            raise LocalizedError("err_box_not_found", kind=NAMES[flavor]) from None
    return r


def _json(r: Any) -> Any:
    try:
        return r.json()
    except ValueError:
        raise LocalizedError("err_unexpected_response") from None


def _signed_in(js: Any, server: str, flavor: str) -> dict:
    user = js.get("User") if isinstance(js, dict) else None
    token = str((js or {}).get("AccessToken") or "") if isinstance(js, dict) else ""
    if not token or not isinstance(user, dict) or not user.get("Id"):
        raise LocalizedError("err_unexpected_response")
    register_secrets(token)
    return {"token": token, "user_id": str(user["Id"]), "user": str(user.get("Name") or ""),
            "server_id": str(js.get("ServerId") or ""), "server": server_url(server, flavor)}


def sign_in(server: str, username: str, password: str, flavor: str = "jellyfin") -> dict:
    """AuthenticateByName: {"token", "user_id", "user", "server_id",
    "server"}. The password goes this once and is kept nowhere."""
    r = _call(flavor, server, "POST", "/Users/AuthenticateByName",
              body={"Username": username, "Pw": password})
    if r.status_code in (400, 401, 403):
        raise LocalizedError("err_wrong_login")
    if r.status_code in (404, 405):
        raise LocalizedError("err_box_not_found", kind=NAMES[flavor])
    r.raise_for_status()
    return _signed_in(_json(r), server, flavor)


def quick_connect_enabled(server: str) -> bool:
    """Whether a Jellyfin server offers Quick Connect (Emby has none)."""
    try:
        r = _call("jellyfin", server, "GET", "/QuickConnect/Enabled", timeout=(4.0, 10.0))
        return r.ok and _json(r) is True
    except LocalizedError:
        return False


def quick_connect_start(server: str) -> dict:
    """A Quick Connect request: {"secret", "code"} - the code is what the
    user types in another app of theirs that is signed in already."""
    r = _call("jellyfin", server, "POST", "/QuickConnect/Initiate")
    if r.status_code in (404, 405):
        r = _call("jellyfin", server, "GET", "/QuickConnect/Initiate")     # 10.7, 10.8
    if r.status_code in (401, 403):
        raise LocalizedError("err_ms_qc_off")
    r.raise_for_status()
    js = _json(r)
    if not isinstance(js, dict) or not js.get("Secret") or not js.get("Code"):
        raise LocalizedError("err_unexpected_response")
    return {"secret": str(js["Secret"]), "code": str(js["Code"])}


def quick_connect_ready(server: str, secret: str) -> bool:
    r = _call("jellyfin", server, "GET", "/QuickConnect/Connect", params={"secret": secret},
              timeout=(4.0, 10.0))
    if r.status_code in (400, 404):
        raise LocalizedError("err_ms_qc_expired")
    r.raise_for_status()
    js = _json(r)
    return bool(isinstance(js, dict) and js.get("Authenticated"))


def quick_connect_finish(server: str, secret: str) -> dict:
    r = _call("jellyfin", server, "POST", "/Users/AuthenticateWithQuickConnect",
              body={"Secret": secret})
    if r.status_code in (400, 401, 403, 404):
        raise LocalizedError("err_ms_qc_expired")
    r.raise_for_status()
    return _signed_in(_json(r), server, "jellyfin")


def _iso(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _major(number: Any) -> int:
    try:
        return int(str(number).split(".")[0].split("-")[0].strip())
    except ValueError:
        return 0


class JellyfinClient(MediaServerClient):
    LOG_NAME = "jellyfin"
    KIND = "Jellyfin"
    FLAVOR = "jellyfin"
    DEFAULT_PORT = DEFAULT_PORT
    # Days of the guide asked for.
    GUIDE_DAYS = 3

    def __init__(self, server: str, token: str, user_id: str = "", account: str = "",
                 options: dict | None = None) -> None:
        super().__init__(server_url(server, self.FLAVOR), token, user_id, account, options)
        self.session.headers["Accept"] = "application/json"
        self.session.headers.update(auth_headers(self.FLAVOR, self.device, self.token))
        self._route_ok: dict[str, int] = {}
        self._policy: dict = {}

    def url(self, path: str) -> str:
        return self.server + _prefix(self.FLAVOR) + (path if path.startswith("/") else "/" + path)

    # -- routes: Jellyfin 10.9 moved what had the user in the path ------------

    def _routes(self, name: str) -> list[tuple[str, bool]]:
        """(path, takes userId as a parameter) to try in turn."""
        uid = quote(self.user_id, safe="")
        old = {"views": f"/Users/{uid}/Views", "items": f"/Users/{uid}/Items",
               "item": f"/Users/{uid}/Items/{{id}}", "resume": f"/Users/{uid}/Items/Resume",
               "played": f"/Users/{uid}/PlayedItems/{{id}}"}[name]
        if self.FLAVOR == "emby":
            return [(old, False)]
        new = {"views": "/UserViews", "items": "/Items", "item": "/Items/{id}",
               "resume": "/UserItems/Resume", "played": "/UserPlayedItems/{id}"}[name]
        return [(new, True), (old, False)]

    def _route(self, name: str, method: str = "GET", params: dict | None = None,
               ident: str = "", timeout: tuple[float, float] | None = None) -> Any:
        routes = self._routes(name)
        first = self._route_ok.get(name, 0)
        order = routes[first:] + routes[:first]
        for i, (path, user_param) in enumerate(order):
            p = dict(params or {})
            if user_param:
                p["userId"] = self.user_id
            try:
                r = self.request(method, path.replace("{id}", quote(ident, safe="")), params=p,
                                 timeout=timeout)
            except requests.HTTPError as e:
                if getattr(e.response, "status_code", 0) in (404, 405) and i + 1 < len(order):
                    continue
                raise
            self._route_ok[name] = routes.index((path, user_param))
            return r
        raise LocalizedError("err_unexpected_response")

    def _items(self, params: dict, route: str = "items") -> list[dict]:
        out: list[dict] = []
        start = 0
        while len(out) < MAX_ITEMS:
            js = self._route(route, params=dict(params, StartIndex=str(start), Limit=str(PAGE)),
                             timeout=self.LIST_TIMEOUT).json()
            rows = js.get("Items") if isinstance(js, dict) else js
            rows = [r for r in rows or [] if isinstance(r, dict)] if isinstance(rows, list) else []
            out += rows
            total = js.get("TotalRecordCount") if isinstance(js, dict) else None
            if len(rows) < PAGE or (isinstance(total, int) and len(out) >= total):
                break
            start += len(rows)
        return out

    # -- who is signed in -----------------------------------------------------

    def _probe(self) -> dict:
        info = self.json("/System/Info/Public")
        if not isinstance(info, dict) or not (info.get("Id") or info.get("ServerName")):
            raise ValueError("not a Jellyfin or Emby server")
        user = self.json(f"/Users/{quote(self.user_id, safe='')}")
        if not isinstance(user, dict) or not user.get("Id"):
            raise LocalizedError("err_ms_signed_out", kind=self.KIND)
        self._policy = as_dict(user.get("Policy"))
        product = str(info.get("ProductName") or self.KIND).replace(" Server", "")
        version = str(info.get("Version") or "")
        name = str(info.get("ServerName") or product)
        return {"kind": self.FLAVOR, "name": f"{name} ({product} {version})".replace(" )", ")"),
                "version": version, "server_id": str(info.get("Id") or ""),
                "user": str(user.get("Name") or self.account)}

    # -- addresses --------------------------------------------------------------

    def _keys(self) -> dict[str, str]:
        if self.FLAVOR == "emby":
            return {"api_key": self.token}
        return {"ApiKey": self.token, "api_key": self.token}

    def _bitrate(self) -> int:
        """The HLS bitrate (bit/s) a film plays at, 0 for the file itself:
        the source's own choice, else the user's remote limit off the home
        network."""
        try:
            mbps = float(str(self.opt("ms_quality", "") or "0"))
        except ValueError:
            mbps = 0.0
        if mbps > 0:
            return int(mbps * 1_000_000)
        host = urlsplit(self.server).hostname or ""
        if is_home_host(host):
            return 0
        limit = to_int(self._policy.get("RemoteClientBitrateLimit")) or 0
        if limit:
            return limit
        return 8_000_000 if self._policy.get("ForceRemoteSourceTranscoding") else 0

    def stream_url(self, iid: str, live: bool = False) -> str:
        bitrate = 0 if live else self._bitrate()
        if bitrate:
            audio = 192_000
            params = {"MediaSourceId": iid, "DeviceId": self.device,
                      "PlaySessionId": uuid.uuid4().hex, "VideoCodec": "h264", "AudioCodec": "aac",
                      "VideoBitrate": str(max(500_000, bitrate - audio)), "AudioBitrate": str(audio),
                      "MaxStreamingBitrate": str(bitrate), "TranscodingMaxAudioChannels": "2",
                      "SegmentContainer": "ts", "MinSegments": "1", "BreakOnNonKeyFrames": "True",
                      **self._keys()}
            return self.url(f"/Videos/{iid}/master.m3u8") + "?" + urlencode(params)
        return self.url(f"/Videos/{iid}/stream") + "?" + urlencode({"static": "true", **self._keys()})

    def image(self, iid: str, tag: Any, kind: str = "Primary", height: int = 480) -> str:
        if not iid or not tag:
            return ""
        return self.url(f"/Items/{iid}/Images/{kind}") + "?" + urlencode(
            {"fillHeight": str(height), "quality": "90", "tag": str(tag)})

    def item_of_url(self, url: str) -> str:
        m = _VIDEO_ID.search(url or "")
        return m.group(1) if m else ""

    # -- what the server has ----------------------------------------------------

    def _views(self) -> list[tuple[str, str, str]]:
        js = self._route("views").json()
        rows = js.get("Items") if isinstance(js, dict) else None
        out = []
        for v in rows_of(rows):
            if v.get("Id"):
                out.append((str(v["Id"]), str(v.get("Name") or ""), str(v.get("CollectionType") or "")))
        return out

    @staticmethod
    def _tmdb(it: dict) -> int | None:
        ids = as_dict(it.get("ProviderIds"))
        return to_int(next((v for k, v in ids.items() if k.lower() == "tmdb"), None))

    def _user_data(self, it: dict) -> dict:
        ud = as_dict(it.get("UserData"))
        return {"played": bool(ud.get("Played")), "pos": ticks(ud.get("PlaybackPositionTicks")),
                "last_played": iso_time(ud.get("LastPlayedDate"))}

    def _art(self, it: dict) -> tuple[str, list[str]]:
        iid = str(it.get("Id") or "")
        tags = as_dict(it.get("ImageTags"))
        icon = self.image(iid, tags.get("Primary"))
        backs = [self.image(iid, t, "Backdrop/0", 720)
                 for t in items(it.get("BackdropImageTags"))[:1]]
        return icon, [b for b in backs if b]

    def _film(self, it: dict, library: str) -> dict:
        iid = str(it.get("Id") or "")
        icon, backdrop = self._art(it)
        return {"key": iid, "title": it.get("Name"), "category": library, "url": self.stream_url(iid),
                "icon": icon, "backdrop": backdrop, "added": iso_time(it.get("DateCreated")),
                "ext": str(it.get("Container") or "mkv").split(",")[0],
                "plot": it.get("Overview"), "genre": ", ".join(str(g) for g in items(it.get("Genres"))),
                "year": it.get("ProductionYear"), "released": str(it.get("PremiereDate") or "")[:10],
                "secs": ticks(it.get("RunTimeTicks")), "rating": it.get("CommunityRating"),
                "official": it.get("OfficialRating"), "tmdb": self._tmdb(it), **self._user_data(it)}

    def _series(self, it: dict, library: str) -> dict:
        icon, backdrop = self._art(it)
        ud = as_dict(it.get("UserData"))
        return {"key": str(it.get("Id") or ""), "title": it.get("Name"), "category": library,
                "icon": icon, "backdrop": backdrop, "added": iso_time(it.get("DateCreated")),
                "updated": iso_time(it.get("DateLastMediaAdded")), "plot": it.get("Overview"),
                "genre": ", ".join(str(g) for g in items(it.get("Genres"))),
                "year": it.get("ProductionYear"), "released": str(it.get("PremiereDate") or "")[:10],
                "rating": it.get("CommunityRating"), "played": bool(ud.get("Played")),
                "tmdb": self._tmdb(it)}

    def _episode(self, it: dict) -> dict:
        iid = str(it.get("Id") or "")
        tags = as_dict(it.get("ImageTags"))
        icon = (self.image(iid, tags.get("Primary"), height=360)
                or self.image(str(it.get("SeriesId") or ""), it.get("SeriesPrimaryImageTag")))
        return {"key": iid, "title": it.get("Name"), "season": it.get("ParentIndexNumber") or 0,
                "episode": it.get("IndexNumber") or 0, "url": self.stream_url(iid), "icon": icon,
                "plot": it.get("Overview"), "secs": ticks(it.get("RunTimeTicks")),
                "released": str(it.get("PremiereDate") or "")[:10],
                "ext": str(it.get("Container") or "mkv").split(",")[0],
                "added": iso_time(it.get("DateCreated")), "series_key": str(it.get("SeriesId") or ""),
                **self._user_data(it)}

    def _catalogue(self) -> dict:
        views = self._views()
        movie_libs = [(i, n) for i, n, t in views if t == "movies"]
        show_libs = [(i, n) for i, n, t in views if t == "tvshows"]
        movies: list[dict] = []
        for lib, _name in movie_libs:
            for it in self._items({"ParentId": lib, "IncludeItemTypes": "Movie", "Recursive": "true",
                                   "Fields": MOVIE_FIELDS, "SortBy": "SortName",
                                   "EnableImageTypes": "Primary,Backdrop", "ImageTypeLimit": "1"}):
                if it.get("Id"):
                    movies.append(self._film(it, lib))
        series: list[dict] = []
        for lib, _name in show_libs:
            for it in self._items({"ParentId": lib, "IncludeItemTypes": "Series", "Recursive": "true",
                                   "Fields": SERIES_FIELDS, "SortBy": "SortName",
                                   "EnableImageTypes": "Primary,Backdrop", "ImageTypeLimit": "1"}):
                if it.get("Id"):
                    series.append(self._series(it, lib))
        return {"movies": movies, "movie_libraries": movie_libs, "series": series,
                "series_libraries": show_libs, "channels": self._channels(),
                "recordings": self._recordings(), "resume": self._resume_list()}

    def _channels(self) -> list[dict]:
        try:
            js = self.json("/LiveTv/Channels", {"userId": self.user_id, "EnableImageTypes": "Primary",
                                                 "ImageTypeLimit": "1", "EnableUserData": "true",
                                                 "SortBy": "SortName", "Limit": "5000"},
                           timeout=self.LIST_TIMEOUT)
        except LocalizedError:
            raise
        except Exception as e:
            log.info("%s: no live TV (%s)", self.LOG_NAME, type(e).__name__)
            return []
        rows = js.get("Items") if isinstance(js, dict) else None
        everything, favs, radio = tr("box_all_channels"), tr("box_favorites"), tr(
            "box_radio", name=tr("box_channels"))
        out: list[dict] = []
        fav_rows: list[dict] = []
        for ch in rows_of(rows):
            if not ch.get("Id"):
                continue
            cid = str(ch["Id"])
            tags = as_dict(ch.get("ImageTags"))
            number = ch.get("ChannelNumber") or ch.get("Number") or ""
            is_radio = str(ch.get("ChannelType") or "").lower() == "radio"
            row = {"key": cid, "name": ch.get("Name") or str(number) or cid,
                   "category": radio if is_radio else everything,
                   "url": self.url(f"/Videos/{cid}/stream") + "?" + urlencode(
                       {"static": "true", **self._keys()}),
                   "icon": self.image(cid, tags.get("Primary"), height=120), "epg_id": cid,
                   "num": _major(number), "_open": True}
            out.append(row)
            ud = as_dict(ch.get("UserData"))
            if ud.get("IsFavorite"):
                fav_rows.append(dict(row, category=favs))
        out.sort(key=lambda r: (r["category"] == radio, r["num"] or 10 ** 9, str(r["name"]).lower()))
        return out + fav_rows

    def _recordings(self) -> list[dict]:
        try:
            js = self.json("/LiveTv/Recordings", {"userId": self.user_id, "IsInProgress": "false",
                                                   "Fields": "Overview", "EnableImageTypes": "Primary"},
                           timeout=self.LIST_TIMEOUT)
        except LocalizedError:
            raise
        except Exception as e:
            log.info("%s: no recordings (%s)", self.LOG_NAME, type(e).__name__)
            return []
        out = []
        for it in rows_of(js.get("Items") if isinstance(js, dict) else None):
            if not it.get("Id"):
                continue
            iid = str(it["Id"])
            title, sub = str(it.get("Name") or ""), str(it.get("EpisodeTitle") or "")
            tags = as_dict(it.get("ImageTags"))
            out.append({"key": iid, "title": f"{title} - {sub}" if title and sub else title or sub,
                        "plot": it.get("Overview") or "", "channel": it.get("ChannelName") or "",
                        "start": iso_time(it.get("StartDate")), "secs": ticks(it.get("RunTimeTicks")),
                        "ext": str(it.get("Container") or "ts").split(",")[0],
                        "icon": self.image(iid, tags.get("Primary")), "url": self.stream_url(iid)})
        return out

    def _resume_list(self) -> list[dict]:
        try:
            js = self._route("resume", params={"MediaTypes": "Video", "Limit": "60",
                                               "Fields": EPISODE_FIELDS,
                                               "EnableImageTypes": "Primary"}).json()
        except LocalizedError:
            raise
        except Exception as e:
            log.info("%s: no resume list (%s)", self.LOG_NAME, type(e).__name__)
            return []
        out = []
        for it in rows_of(js.get("Items") if isinstance(js, dict) else None):
            if not it.get("Id"):
                continue
            kind = str(it.get("Type") or "")
            if kind == "Episode" and it.get("SeriesId"):
                out.append(self._episode(it))
            elif kind == "Movie":
                out.append(self._film(it, ""))
        return out

    def _episodes_of(self, series_key: str) -> list[dict]:
        js = self.json(f"/Shows/{quote(series_key, safe='')}/Episodes",
                       {"userId": self.user_id, "Fields": EPISODE_FIELDS,
                        "EnableImageTypes": "Primary", "ImageTypeLimit": "1"},
                       timeout=self.LIST_TIMEOUT)
        rows = js.get("Items") if isinstance(js, dict) else None
        return [self._episode(it) for it in rows_of(rows) if it.get("Id")]

    def _details(self, key: str) -> dict:
        it = self._route("item", ident=key, params={"Fields": "People,Genres,Studios,Overview"}).json()
        if not isinstance(it, dict):
            return {}
        people = [p for p in rows_of(it.get("People")) if p.get("Name")]
        return {"cast": ", ".join(str(p["Name"]) for p in people if p.get("Type") == "Actor"),
                "director": ", ".join(str(p["Name"]) for p in people if p.get("Type") == "Director")}

    # -- live TV -----------------------------------------------------------------

    def _live_open(self, row: dict) -> str:
        """PlaybackInfo with the live stream opened: the stream as it comes
        where the server lets it, else its HLS."""
        cid = str(row["_key"])
        js = self.json(f"/Items/{quote(cid, safe='')}/PlaybackInfo",
                       {"userId": self.user_id, "StartTimeTicks": "0", "IsPlayback": "true",
                        "AutoOpenLiveStream": "true"}, method="POST",
                       body={"UserId": self.user_id, "DeviceProfile": DEVICE_PROFILE,
                             "AutoOpenLiveStream": True, "IsPlayback": True})
        sources = js.get("MediaSources") if isinstance(js, dict) else None
        ms = as_dict(sources[0]) if isinstance(sources, list) and sources else {}
        session = str(as_dict(js).get("PlaySessionId") or uuid.uuid4().hex)
        if ms.get("SupportsDirectStream") or ms.get("SupportsDirectPlay"):
            params = {"static": "true", "MediaSourceId": str(ms.get("Id") or cid),
                      "PlaySessionId": session, "DeviceId": self.device}
            if ms.get("LiveStreamId"):
                params["LiveStreamId"] = str(ms["LiveStreamId"])
            container = re.sub(r"[^a-z0-9]", "", str(ms.get("Container") or "ts").split(",")[0].lower()) or "ts"
            return self.url(f"/Videos/{cid}/stream.{container}") + "?" + urlencode(
                {**params, **self._keys()})
        tu = str(ms.get("TranscodingUrl") or "")
        if tu:
            url = self.url(tu) if tu.startswith("/") else tu
            if "api_key=" not in url.lower():
                url += ("&" if "?" in url else "?") + urlencode(self._keys())
            return url
        log.info("%s: the server gave no stream for a channel - played as the file", self.LOG_NAME)
        return str(row.get("_url") or "")

    def _guide(self, progress: Callable[[int], None] | None) -> bytes:
        channels = self.guide_channels()
        if not channels:
            return b""
        now = int(time.time())
        days = max(1, min(14, self.opt_int("guide_days", self.GUIDE_DAYS)))
        params = {"userId": self.user_id, "MinEndDate": _iso(now - 3600),
                  "MaxStartDate": _iso(now + days * 86400), "Fields": "Overview,Genres",
                  "EnableImages": "false", "EnableUserData": "false", "SortBy": "StartDate"}
        events: list[dict] = []
        start, page = 0, 2000
        while start < 200_000:
            js = self.json("/LiveTv/Programs", dict(params, StartIndex=str(start), Limit=str(page)),
                           timeout=(6.0, 120.0))
            rows = rows_of(js.get("Items") if isinstance(js, dict) else None)
            for p in rows:
                events.append({"channel": str(p.get("ChannelId") or ""), "start": iso_time(p.get("StartDate")),
                               "stop": iso_time(p.get("EndDate")), "title": str(p.get("Name") or ""),
                               "subtitle": str(p.get("EpisodeTitle") or ""),
                               "desc": str(p.get("Overview") or ""),
                               "category": ", ".join(str(g) for g in items(p.get("Genres")))})
            total = js.get("TotalRecordCount") if isinstance(js, dict) else None
            start += len(rows)
            if progress and isinstance(total, int) and total:
                progress(min(99, start * 100 // total))
            if len(rows) < page or (isinstance(total, int) and start >= total):
                break
        wanted = {cid for cid, _ in channels}
        events = [e for e in events if e["channel"] in wanted and e["start"] and e["stop"]]
        log.info("%s: %d programme(s) for %d channel(s)", self.LOG_NAME, len(events), len(wanted))
        return build_xmltv(channels, events) if events else b""

    # -- what is played, back to the server -----------------------------------

    def _send(self, job: tuple) -> None:
        if job[0] == "mark":
            _, played, target = job
            self._route("played", method="POST" if played else "DELETE", ident=str(target["id"]))
            return
        _, event, target, pos, _dur, paused = job
        q = target.get("query") or {}
        body: dict[str, Any] = {
            "ItemId": target["id"], "PositionTicks": int(pos * 10_000_000), "IsPaused": paused,
            "CanSeek": target.get("kind") != "live", "PlaySessionId": target.get("session") or "",
            "MediaSourceId": q.get("mediasourceid") or target["id"],
            "PlayMethod": "Transcode" if target.get("hls") else "DirectStream"}
        if q.get("livestreamid"):
            body["LiveStreamId"] = q["livestreamid"]
        if event == "progress":
            body["EventName"] = "Pause" if paused else "TimeUpdate"
        path = {"start": "/Sessions/Playing", "progress": "/Sessions/Playing/Progress",
                "stop": "/Sessions/Playing/Stopped"}[event]
        self.request("POST", path, body=body)


class EmbyClient(JellyfinClient):
    LOG_NAME = "emby"
    KIND = "Emby"
    FLAVOR = "emby"


# -- discovery on the local network (UDP 7359) ---------------------------------

def parse_discovery(data: bytes, ip: str, flavor: str) -> dict | None:
    """A server's answer to "who is JellyfinServer?" (or EmbyServer) as
    {"kind", "server", "name", "id"}, or None. A server in a container names
    an address of its own: the one that answered is the one to use then."""
    try:
        js = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):    # a datagram of "[[[[..." from anyone on the network
        return None
    if not isinstance(js, dict) or not (js.get("Address") or js.get("Id")):
        return None
    addr = str(js.get("Address") or "").strip().rstrip("/")
    server = f"http://{ip}:{DEFAULT_PORT}"
    if addr:
        try:
            u = urlsplit(addr if "://" in addr else "http://" + addr)
            host = u.hostname or ""
            port = u.port
        except ValueError:
            host, port, u = "", None, None
        if u is not None and host and u.scheme.lower() in ("http", "https"):
            private = is_home_host(host) and "." in host
            netloc = (f"{ip}:{port}" if port else ip) if private and host != ip else u.netloc
            server = urlunsplit((u.scheme or "http", netloc, u.path, "", ""))
    return {"kind": flavor, "server": server, "id": str(js.get("Id") or ""),
            "name": f"{js.get('Name') or NAMES[flavor]} - {ip}"}


def discover(timeout: float = 1.5, target: tuple[str, int] = ("255.255.255.255", DISCOVERY_PORT)
             ) -> list[dict]:
    """Every Jellyfin and Emby server that answers a broadcast here."""
    found: dict[str, dict] = {}
    socks: dict[Any, str] = {}
    try:
        for flavor, question in QUESTIONS.items():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except OSError:
                continue
            socks[s] = flavor
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.setblocking(False)
                s.sendto(question, target)
            except OSError as e:
                log.debug("jellyfin: discovery (%s): %s", flavor, e)
        end = time.monotonic() + timeout
        while socks:
            left = end - time.monotonic()
            if left <= 0:
                break
            try:
                ready, _, _ = select.select(list(socks), [], [], left)
            except (OSError, ValueError):
                break
            for s in ready:
                try:
                    data, (ip, _port) = s.recvfrom(4096)
                except OSError:
                    continue
                got = parse_discovery(data, ip, socks[s])
                if got:
                    ident = got["id"] or got["server"]
                    # The one that answered as Jellyfin is Jellyfin.
                    if ident not in found or got["kind"] == "jellyfin":
                        found[ident] = got
    finally:
        for s in socks:
            s.close()
    return list(found.values())
