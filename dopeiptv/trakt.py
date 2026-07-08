"""Trakt.tv integration: device-code auth, scrobbling, watchlist/history."""

from __future__ import annotations

import time
from typing import Any

import requests
from PyQt6.QtCore import QSettings

API = "https://api.trakt.tv"
API_VERSION = "2"


class TraktAuthError(Exception):
    pass


class TraktClient:
    """Trakt API client: device auth, token storage, scrobbling, lookups."""

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings

    # -- credentials / tokens (persisted via QSettings) ---------------------

    @property
    def client_id(self) -> str:
        return self.settings.value("trakt_client_id", "") or ""

    @property
    def client_secret(self) -> str:
        return self.settings.value("trakt_client_secret", "") or ""

    @property
    def access_token(self) -> str:
        return self.settings.value("trakt_access_token", "") or ""

    @property
    def refresh_token(self) -> str:
        return self.settings.value("trakt_refresh_token", "") or ""

    def is_connected(self) -> bool:
        return bool(self.access_token and self.client_id)

    def disconnect(self) -> None:
        for k in ("trakt_access_token", "trakt_refresh_token",
                  "trakt_expires_at"):
            self.settings.remove(k)

    def _store_tokens(self, data: dict) -> None:
        self.settings.setValue("trakt_access_token", data.get("access_token", ""))
        self.settings.setValue("trakt_refresh_token", data.get("refresh_token", ""))
        self.settings.setValue(
            "trakt_expires_at",
            int(time.time()) + int(data.get("expires_in", 0)))

    # -- device-code OAuth flow ----------------------------------------------

    def start_device_auth(self) -> dict:
        """Step 1: request a device+user code. Returns the API response."""
        r = requests.post(
            f"{API}/oauth/device/code",
            json={"client_id": self.client_id}, timeout=10)
        r.raise_for_status()
        return r.json()

    def poll_device_token(self, device_code: str) -> dict | None:
        """Step 2: poll for the token. None while pending, dict on success.

        Raises TraktAuthError on denial/expiry.
        """
        r = requests.post(
            f"{API}/oauth/device/token",
            json={"code": device_code, "client_id": self.client_id,
                  "client_secret": self.client_secret},
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            self._store_tokens(data)
            return data
        if r.status_code == 400:
            return None  # authorization pending
        if r.status_code in (404, 409, 410, 418):
            raise TraktAuthError({
                404: "Invalid device code.",
                409: "Code already used.",
                410: "Code expired - try again.",
                418: "Authorization denied.",
            }[r.status_code])
        r.raise_for_status()
        return None

    # -- headers --------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "trakt-api-version": API_VERSION,
            "trakt-api-key": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

    # -- id resolution (best-effort title search) ----------------------------

    def find_movie(self, title: str) -> dict | None:
        r = requests.get(f"{API}/search/movie",
                         params={"query": title, "limit": 1},
                         headers=self._headers(), timeout=10)
        r.raise_for_status()
        results = r.json() or []
        return results[0]["movie"] if results else None

    def find_episode(self, show_title: str, season: int,
                     episode: int) -> dict | None:
        """Resolve a show by title, then fetch the episode's own ids.

        Returns the episode dict (with its own "ids") - Trakt's scrobble
        endpoint only needs {"episode": {...}}, it infers the show.
        """
        r = requests.get(f"{API}/search/show",
                         params={"query": show_title, "limit": 1},
                         headers=self._headers(), timeout=10)
        r.raise_for_status()
        results = r.json() or []
        if not results:
            return None
        show_id = results[0]["show"]["ids"]["trakt"]
        r2 = requests.get(
            f"{API}/shows/{show_id}/seasons/{season}/episodes/{episode}",
            headers=self._headers(), timeout=10)
        if r2.status_code != 200:
            return None
        return r2.json()

    # -- scrobbling -----------------------------------------------------------

    def _scrobble(self, action: str, payload: dict) -> None:
        requests.post(f"{API}/scrobble/{action}", json=payload,
                      headers=self._headers(), timeout=10)

    def scrobble_start(self, item_payload: dict, progress: float = 0.0) -> None:
        self._scrobble("start", {**item_payload, "progress": progress})

    def scrobble_stop(self, item_payload: dict, progress: float = 0.0) -> None:
        self._scrobble("stop", {**item_payload, "progress": progress})

    # -- watchlist / history --------------------------------------------------

    def watchlist(self) -> list[dict]:
        r = requests.get(f"{API}/sync/watchlist",
                         headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json() or []

    def history(self, limit: int = 50) -> list[dict]:
        r = requests.get(f"{API}/sync/history",
                         params={"limit": limit},
                         headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json() or []
