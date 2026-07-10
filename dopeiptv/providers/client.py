"""Xtream Codes API client and EPG helper functions."""

from __future__ import annotations

import base64
import html
import re
from datetime import datetime, timezone
from typing import Any

import requests


class XtreamClient:
    """HTTP client for the Xtream Codes player_api.php endpoint."""

    def __init__(self, server: str, username: str, password: str) -> None:
        self.server = server.rstrip("/")
        if not self.server.startswith(("http://", "https://")):
            self.server = "http://" + self.server
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "dopeIPTV/1.0"

    def _api(self, **params: Any) -> Any:
        url = f"{self.server}/player_api.php"
        base: dict[str, Any] = {"username": self.username, "password": self.password}
        base.update(params)
        r = self.session.get(url, params=base, timeout=20)
        r.raise_for_status()
        return r.json()

    def authenticate(self) -> dict:
        data = self._api()
        if not isinstance(data, dict) or "user_info" not in data:
            raise RuntimeError("Unexpected response from the server.")
        if str(data["user_info"].get("auth", 0)) != "1":
            raise RuntimeError("Wrong username or password.")
        return data

    def live_categories(self) -> list[dict]:
        return self._api(action="get_live_categories") or []

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_live_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def vod_categories(self) -> list[dict]:
        return self._api(action="get_vod_categories") or []

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_vod_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def series_categories(self) -> list[dict]:
        return self._api(action="get_series_categories") or []

    def series_list(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_series"}
        if category_id:
            p["category_id"] = category_id
        return self._api(**p) or []

    def series_info(self, series_id: int | str) -> dict:
        return self._api(action="get_series_info", series_id=series_id) or {}

    def vod_info(self, vod_id: int | str) -> dict:
        return self._api(action="get_vod_info", vod_id=vod_id) or {}

    def short_epg(self, stream_id: int | str, limit: int = 8) -> list[dict]:
        data = self._api(action="get_short_epg", stream_id=stream_id, limit=limit)
        return (data or {}).get("epg_listings", [])

    def epg_table(self, stream_id: int | str) -> list[dict]:
        """Full EPG table - fallback when get_short_epg returns nothing."""
        data = self._api(action="get_simple_data_table", stream_id=stream_id)
        return (data or {}).get("epg_listings", [])

    def xmltv(self) -> bytes:
        """The provider's full XMLTV guide."""
        r = self.session.get(f"{self.server}/xmltv.php",
                             params={"username": self.username,
                                     "password": self.password},
                             timeout=(20, 180))
        r.raise_for_status()
        return r.content

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        ext = "m3u8" if fmt == "m3u8" else "ts"
        return f"{self.server}/live/{self.username}/{self.password}/{stream_id}.{ext}"

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        ext = ext or "mp4"
        return f"{self.server}/movie/{self.username}/{self.password}/{stream_id}.{ext}"

    def episode_url(self, episode_id: int | str, ext: str | None = None) -> str:
        ext = ext or "mp4"
        return f"{self.server}/series/{self.username}/{self.password}/{episode_id}.{ext}"

    def timeshift_url(self, stream_id: int | str, start_dt: datetime,
                      duration_min: int) -> str:
        stamp = start_dt.strftime("%Y-%m-%d:%H-%M")
        return (f"{self.server}/timeshift/{self.username}/{self.password}/"
                f"{int(duration_min)}/{stamp}/{stream_id}.ts")


class OfflineClient:
    """A do-nothing stand-in for :class:`XtreamClient`, used when the app is
    opened without a provider (first-run "explore" mode).

    It exposes the exact same interface but every data call returns empty, so
    the whole UI works with no server configured - lists are simply empty and
    nothing hits the network. It is a Null Object: the window never has to
    special-case a missing client, and swapping in a real client later
    (``MainWindow.switch_playlist``) needs no teardown.
    """

    def __init__(self) -> None:
        self.server = ""
        self.username = ""
        self.password = ""
        self.session = requests.Session()

    def authenticate(self) -> dict:
        return {}

    def live_categories(self) -> list[dict]:
        return []

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        return []

    def vod_categories(self) -> list[dict]:
        return []

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        return []

    def series_categories(self) -> list[dict]:
        return []

    def series_list(self, category_id: str | None = None) -> list[dict]:
        return []

    def series_info(self, series_id: int | str) -> dict:
        return {}

    def vod_info(self, vod_id: int | str) -> dict:
        return {}

    def short_epg(self, stream_id: int | str, limit: int = 8) -> list[dict]:
        return []

    def epg_table(self, stream_id: int | str) -> list[dict]:
        return []

    def xmltv(self) -> bytes:
        return b""

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        return ""

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        return ""

    def episode_url(self, episode_id: int | str, ext: str | None = None) -> str:
        return ""

    def timeshift_url(self, stream_id: int | str, start_dt: datetime,
                      duration_min: int) -> str:
        return ""


class M3UClient(OfflineClient):
    """Backed by a plain M3U/M3U8 playlist (a URL or local file) instead of an
    Xtream API. Exposes the same interface as XtreamClient but Live-only -
    M3U lists have no movies/series/EPG API, so those stay empty (the guide
    can still come from the playlist's separate EPG URL). Channels are grouped
    by their ``group-title`` into categories."""

    _EXTINF = re.compile(r'#EXTINF:-?\d*\s*(?P<attrs>[^,]*),(?P<name>.*)')
    _ATTR = re.compile(r'([\w-]+)="([^"]*)"')

    def __init__(self, url: str) -> None:
        super().__init__()
        self.server = (url or "").strip()
        self._channels: list[dict] = []
        self._loaded = False

    # -- fetch + parse --------------------------------------------------------

    def _fetch(self) -> str:
        url = self.server
        if url.startswith(("http://", "https://")):
            r = self.session.get(url, timeout=(20, 180))
            r.raise_for_status()
            return r.text
        if url.startswith("file://"):
            url = url[7:]
        with open(url, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def _parse(self, text: str) -> None:
        channels: list[dict] = []
        pending: dict | None = None
        sid = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                m = self._EXTINF.match(line)
                if not m:
                    pending = None
                    continue
                attrs = dict(self._ATTR.findall(m.group("attrs")))
                name = (m.group("name").strip()
                        or attrs.get("tvg-name", "")).strip() or "?"
                pending = {
                    "name": name,
                    "stream_icon": attrs.get("tvg-logo", ""),
                    "epg_channel_id": attrs.get("tvg-id", ""),
                    "category_name": attrs.get("group-title", "").strip()
                    or "Uncategorized",
                }
            elif line.startswith("#"):
                continue                       # other directives: ignore
            elif pending is not None:
                sid += 1
                pending["stream_id"] = sid
                pending["num"] = sid
                pending["category_id"] = pending["category_name"]
                pending["_url"] = line
                channels.append(pending)
                pending = None
        self._channels = channels

    def authenticate(self) -> dict:
        # Fetching + parsing the list is this provider's "auth": it proves the
        # URL is reachable and yields at least one channel.
        self._parse(self._fetch())
        self._loaded = True
        if not self._channels:
            raise RuntimeError("No channels found in the M3U playlist.")
        return {"user_info": {"auth": 1}}

    def _ensure(self) -> None:
        if not self._loaded:
            try:
                self.authenticate()
            except Exception:
                self._loaded = True            # don't retry-storm on failure

    # -- interface ------------------------------------------------------------

    def live_categories(self) -> list[dict]:
        self._ensure()
        seen: list[str] = []
        for c in self._channels:
            if c["category_id"] not in seen:
                seen.append(c["category_id"])
        return [{"category_id": g, "category_name": g} for g in seen]

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        self._ensure()
        return [c for c in self._channels
                if category_id is None or c["category_id"] == category_id]

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        self._ensure()
        try:
            sid = int(stream_id)
        except (TypeError, ValueError):
            return ""
        for c in self._channels:
            if c["stream_id"] == sid:
                return c["_url"]
        return ""


def make_client(playlist: dict):
    """Build the right provider client for a stored playlist: an M3U-backed
    client when kind == 'm3u', otherwise the Xtream API client."""
    if (playlist or {}).get("kind") == "m3u":
        return M3UClient(playlist.get("server", ""))
    return XtreamClient(playlist["server"], playlist["username"],
                        playlist["password"])


class DemoClient(OfflineClient):
    """A no-account 'try it out' provider: one Live category of official,
    free, public HLS test streams so the app can be exercised end to end
    without any real credentials. Everything else (movies, series, EPG) is
    empty, inherited from OfflineClient. These are well-known test assets
    (Mux, Apple, Bitmovin) - not a third-party IPTV service."""

    # (name, HLS url). Kept deliberately small and to stable public streams.
    STREAMS: list[tuple[str, str]] = [
        ("Big Buck Bunny",
         "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"),
        ("Apple BipBop",
         "https://devstreaming-cdn.apple.com/videos/streaming/examples/"
         "bipbop_4x3/bipbop_4x3_variant.m3u8"),
        ("Sintel",
         "https://bitdash-a.akamaihd.net/content/sintel/hls/playlist.m3u8"),
        ("Art of Motion",
         "https://bitdash-a.akamaihd.net/content/MI201109210084_1/m3u8s/"
         "f08e80da-bf1d-4e3d-8899-f0f6155f6efa.m3u8"),
        ("Tears of Steel",
         "https://test-streams.mux.dev/tos_ismc/main.m3u8"),
    ]
    CATEGORY_ID = "demo"

    def __init__(self) -> None:
        super().__init__()
        # stream_id -> url, with 1-based ids matching the list order.
        self._urls = {i + 1: url for i, (_n, url) in enumerate(self.STREAMS)}

    def live_categories(self) -> list[dict]:
        return [{"category_id": self.CATEGORY_ID,
                 "category_name": "Demo channels"}]

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        return [{"stream_id": i + 1, "num": i + 1, "name": name,
                 "stream_icon": "", "category_id": self.CATEGORY_ID,
                 "epg_channel_id": ""}
                for i, (name, _url) in enumerate(self.STREAMS)]

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        try:
            return self._urls.get(int(stream_id), "")
        except (TypeError, ValueError):
            return ""


def b64(text: str | None) -> str:
    """Decode Xtream's base64-encoded EPG text fields."""
    if not text:
        return ""
    try:
        return html.unescape(base64.b64decode(text).decode("utf-8", "replace")).strip()
    except Exception:
        return str(text)


def epg_times(entry: dict) -> tuple[datetime | None, datetime | None]:
    """Returns (start, stop) as local datetimes, or (None, None)."""
    def parse(ts_key: str, str_key: str) -> datetime | None:
        v = entry.get(ts_key)
        if v:
            try:
                return datetime.fromtimestamp(int(v), tz=timezone.utc).astimezone()
            except Exception:
                pass
        v = entry.get(str_key)
        if v:
            for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(v, f).astimezone()
                except Exception:
                    continue
        return None
    return parse("start_timestamp", "start"), parse("stop_timestamp", "end")
