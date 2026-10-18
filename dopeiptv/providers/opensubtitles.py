"""OpenSubtitles.com: find and fetch subtitles for what is playing.

The REST API at api.opensubtitles.com/api/v1. Every request carries an API
key - the user's own from Settings, or one baked into release builds - and a
User-Agent naming the app, as the API asks of every client. Signing in is
optional: it only raises how many subtitles a day can be downloaded.
Synchronous throughout; the window runs it on a worker thread.
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path
from typing import Any

from .. import APP_NAME, VERSION
from ..core._lazy_requests import requests
from ..core.log import log
from ..i18n import LocalizedError

API = "https://api.opensubtitles.com/api/v1"
TIMEOUT = (10, 30)
# A subtitle is text; anything this big is not one.
MAX_BYTES = 8 * 1024 * 1024
HASH_CHUNK = 65536

# Languages offered for a search, by OpenSubtitles' own codes - two letters,
# and a region where the site keeps two variants apart.
LANGUAGES = (
    "ar", "bg", "bs", "ca", "cs", "da", "de", "el", "en", "es", "et", "eu",
    "fa", "fi", "fr", "he", "hi", "hr", "hu", "id", "is", "it", "ja", "ko",
    "lt", "lv", "mk", "ms", "nl", "no", "pl", "pt-BR", "pt-PT", "ro", "ru",
    "sk", "sl", "sq", "sr", "sv", "sw", "th", "tr", "uk", "ur", "vi",
    "zh-CN", "zh-TW",
)
# The player's language settings are ISO 639-2 (what mpv's slang takes).
_FROM_639_2 = {
    "ara": "ar", "dan": "da", "eng": "en", "fin": "fi", "fre": "fr",
    "fra": "fr", "ger": "de", "deu": "de", "ita": "it", "nor": "no",
    "nob": "no", "pol": "pl", "por": "pt-PT", "spa": "es", "swe": "sv",
    "tur": "tr",
}
# The app's own languages, where the site's code differs.
_FROM_UI = {"pt": "pt-BR", "zh": "zh-CN"}


def bundled_key() -> str:
    """The API key release builds ship with, so subtitles work without an
    account of one's own. From DOPEIPTV_OPENSUBTITLES_KEY or a git-ignored
    dopeiptv/_opensubtitles_key.py - never from the public repository. A
    key entered in Settings wins over it."""
    key = (os.environ.get("DOPEIPTV_OPENSUBTITLES_KEY") or "").strip()
    if key:
        return key
    try:
        from .._opensubtitles_key import OPENSUBTITLES_API_KEY  # type: ignore
        return (OPENSUBTITLES_API_KEY or "").strip()
    except Exception:
        return ""


def search_language(sub_lang: str, ui_lang: str) -> str:
    """The language to search in first: the subtitle language picked in
    Settings, else the language the app itself is in."""
    code = _FROM_639_2.get((sub_lang or "").lower())
    if code:
        return code
    ui = (ui_lang or "en").split("_")[0].lower()
    code = _FROM_UI.get(ui, ui)
    return code if code in LANGUAGES else "en"


def movie_hash(path: str | Path) -> str | None:
    """OpenSubtitles' hash of a local video: its size plus the sums of the
    64-bit words in its first and last 64 KiB, as 16 hex digits. Finds the
    subtitle made for this exact file. None for a file too small to hash."""
    try:
        size = os.path.getsize(path)
        if size < 2 * HASH_CHUNK:
            return None
        value = size
        with open(path, "rb") as fh:
            for offset in (0, size - HASH_CHUNK):
                fh.seek(offset)
                for (word,) in struct.iter_unpack("<Q", fh.read(HASH_CHUNK)):
                    value = (value + word) & 0xFFFFFFFFFFFFFFFF
        return f"{value:016x}"
    except OSError:
        return None


def _row(entry: Any) -> dict | None:
    """One search hit, slimmed to what the dialog shows and download needs.
    A subtitle split over several files (CD1/CD2) fits no single stream."""
    attrs = (entry or {}).get("attributes") if isinstance(entry, dict) else None
    if not isinstance(attrs, dict):
        return None
    files = attrs.get("files") or []
    if len(files) != 1 or not isinstance(files[0], dict):
        return None
    try:
        file_id = int(files[0].get("file_id") or "")
        downloads = int(attrs.get("download_count") or 0)
    except (TypeError, ValueError):
        return None
    feature = attrs.get("feature_details") or {}
    return {
        "file_id": file_id,
        "language": str(attrs.get("language") or ""),
        "release": str(attrs.get("release") or files[0].get("file_name")
                       or ""),
        "downloads": downloads,
        "hearing_impaired": bool(attrs.get("hearing_impaired")),
        "machine": bool(attrs.get("machine_translated")
                        or attrs.get("ai_translated")),
        "trusted": bool(attrs.get("from_trusted")),
        "hash_match": bool(attrs.get("moviehash_match")),
        "title": str(feature.get("title") or feature.get("movie_name")
                     or ""),
    }


def rank(rows: list[dict]) -> list[dict]:
    """Made for this very file first, then people over machines, trusted
    uploaders, and what most others downloaded."""
    return sorted(rows, key=lambda r: (not r["hash_match"], r["machine"],
                                       not r["trusted"], -r["downloads"]))


class OpenSubtitles:
    """A client for one key (and, optionally, one account)."""

    def __init__(self, api_key: str, username: str = "", password: str = "",
                 session: Any = None) -> None:
        self.api_key = (api_key or "").strip()
        self.username = (username or "").strip()
        self.password = password or ""
        self.session = session if session is not None else requests.Session()
        self._token = ""
        self._base = API
        # What the last download left of the day's allowance, if it said.
        self.remaining: int | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Api-Key": self.api_key,
                   "User-Agent": f"{APP_NAME} v{VERSION}",
                   "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _answer(r: Any) -> Any:
        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code < 400:
            return data
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("message") or data.get("error") or "")
            errors = data.get("errors")
            if not msg and isinstance(errors, list) and errors:
                msg = "; ".join(str(e) for e in errors)
        if r.status_code in (401, 403) and not msg:
            err = LocalizedError("subs_err_key")
        elif msg:
            # The site's own words: which limit was hit and when it resets.
            err = LocalizedError(msg)
        else:
            err = LocalizedError("subs_err_http", code=r.status_code)
        err.status = r.status_code  # type: ignore[attr-defined]
        raise err

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.api_key:
            raise LocalizedError("subs_need_key")
        try:
            r = self.session.request(method, f"{self._base}{path}",
                                     headers=self._headers(),
                                     timeout=TIMEOUT, **kwargs)
        except requests.RequestException as e:
            log.info("opensubtitles: %s %s failed: %s", method, path, e)
            raise LocalizedError("subs_err_network") from e
        return self._answer(r)

    def login(self) -> None:
        """Sign in, so downloads count against the account's allowance."""
        data = self._call("POST", "/login", json={
            "username": self.username, "password": self.password}) or {}
        self._token = str(data.get("token") or "")
        base = str(data.get("base_url") or "")
        # Paying members are sent to a host of their own. Only ever one of
        # the site's: the password went in the request above, the token
        # goes with every one after it.
        if re.fullmatch(r"(?:[a-z0-9-]+\.)*opensubtitles\.com", base):
            self._base = f"https://{base}/api/v1"

    def search(self, *, languages: str = "", query: str = "",
               tmdb_id: int | None = None,
               parent_tmdb_id: int | None = None,
               season: int | None = None, episode: int | None = None,
               moviehash: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if languages:
            params["languages"] = ",".join(sorted(languages.split(",")))
        if moviehash:
            params["moviehash"] = moviehash
        if tmdb_id:
            params["tmdb_id"] = int(tmdb_id)
        elif parent_tmdb_id:
            params["parent_tmdb_id"] = int(parent_tmdb_id)
            if season is not None:
                params["season_number"] = int(season)
                if episode is not None:
                    params["episode_number"] = int(episode)
        if query and not (tmdb_id or parent_tmdb_id):
            params["query"] = query.strip().lower()
        # The API answers a request with its parameters out of order with a
        # redirect to the sorted one: sorted here, it is one round trip.
        data = self._call("GET", "/subtitles",
                          params=sorted(params.items())) or {}
        rows = [r for r in (_row(e) for e in data.get("data") or []) if r]
        return rank(rows)

    def download(self, file_id: int) -> tuple[str, bytes]:
        """(file name, subtitle bytes). One of the day's downloads."""
        if self.username and self.password and not self._token:
            self.login()
        try:
            data = self._call("POST", "/download",
                              json={"file_id": int(file_id)}) or {}
        except LocalizedError as e:
            # A sign-in lasts a day; the app may run longer than that.
            if getattr(e, "status", None) != 401 or not self._token:
                raise
            self._token = ""
            self.login()
            data = self._call("POST", "/download",
                              json={"file_id": int(file_id)}) or {}
        link = str(data.get("link") or "")
        if not link.startswith("https://"):
            raise LocalizedError("subs_err_bad_file")
        left = data.get("remaining")
        self.remaining = left if isinstance(left, int) else None
        try:
            r = self.session.get(
                link, timeout=TIMEOUT, stream=True,
                headers={"User-Agent": f"{APP_NAME} v{VERSION}"})
            r.raise_for_status()
            body = bytearray()
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > MAX_BYTES:
                    raise LocalizedError("subs_err_bad_file")
        except requests.RequestException as e:
            log.info("opensubtitles: fetching the file failed: %s", e)
            raise LocalizedError("subs_err_network") from e
        name = str(data.get("file_name") or f"{file_id}.srt")
        return name, bytes(body)
