"""Xtream Codes API client and EPG helper functions."""

from __future__ import annotations

import base64
import html
import os
import shutil
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


def find_player_executable(player: str) -> str | None:
    """Locate the mpv or vlc binary."""
    if player == "mpv":
        candidates = ["mpv"]
    else:
        candidates = ["vlc", "cvlc"]
    for c in candidates:
        if os.path.isabs(c):
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None
