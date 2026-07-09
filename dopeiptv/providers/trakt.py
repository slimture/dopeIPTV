"""Trakt.tv integration: device-code auth, scrobbling, watchlist/history."""

from __future__ import annotations

import time

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

    # -- watched (for cross-device sync into the "already seen" indicator) ----

    def watched_movies(self) -> list[int]:
        """Every movie the user has marked watched on any device.
        Returns the list of TMDB ids - dopeIPTV keys its local titles on
        TMDB via the poster-resolver, so imdb ids aren't needed."""
        r = requests.get(f"{API}/sync/watched/movies",
                         headers=self._headers(), timeout=30)
        r.raise_for_status()
        out: list[int] = []
        for entry in r.json() or []:
            tid = ((entry.get("movie") or {}).get("ids") or {}).get("tmdb")
            if isinstance(tid, int):
                out.append(tid)
        return out

    # -- add/remove history (mark-as-watched from the app) -------------------

    @staticmethod
    def _movie_payload(tmdb_id: int) -> dict:
        return {"movies": [{"ids": {"tmdb": int(tmdb_id)}}]}

    @staticmethod
    def _episode_payload(show_tmdb_id: int, season: int,
                         episode: int) -> dict:
        return {"shows": [{
            "ids": {"tmdb": int(show_tmdb_id)},
            "seasons": [{
                "number": int(season),
                "episodes": [{"number": int(episode)}],
            }],
        }]}

    def add_movie_history(self, tmdb_id: int) -> None:
        r = requests.post(f"{API}/sync/history",
                          json=self._movie_payload(tmdb_id),
                          headers=self._headers(), timeout=15)
        r.raise_for_status()

    def remove_movie_history(self, tmdb_id: int) -> None:
        r = requests.post(f"{API}/sync/history/remove",
                          json=self._movie_payload(tmdb_id),
                          headers=self._headers(), timeout=15)
        r.raise_for_status()

    def add_show_history(self, show_tmdb_id: int) -> None:
        """Mark an entire show watched. A show payload with no seasons
        tells Trakt to add every aired episode to the history - the
        closest thing to a 'seen the whole series' primitive."""
        r = requests.post(
            f"{API}/sync/history",
            json={"shows": [{"ids": {"tmdb": int(show_tmdb_id)}}]},
            headers=self._headers(), timeout=15)
        r.raise_for_status()

    def remove_show_history(self, show_tmdb_id: int) -> None:
        r = requests.post(
            f"{API}/sync/history/remove",
            json={"shows": [{"ids": {"tmdb": int(show_tmdb_id)}}]},
            headers=self._headers(), timeout=15)
        r.raise_for_status()

    def add_episode_history(self, show_tmdb_id: int, season: int,
                            episode: int) -> None:
        r = requests.post(
            f"{API}/sync/history",
            json=self._episode_payload(show_tmdb_id, season, episode),
            headers=self._headers(), timeout=15)
        r.raise_for_status()

    def remove_episode_history(self, show_tmdb_id: int, season: int,
                               episode: int) -> None:
        r = requests.post(
            f"{API}/sync/history/remove",
            json=self._episode_payload(show_tmdb_id, season, episode),
            headers=self._headers(), timeout=15)
        r.raise_for_status()

    # -- watchlist (add-to-watch-later) --------------------------------------

    def add_movie_watchlist(self, tmdb_id: int) -> None:
        r = requests.post(f"{API}/sync/watchlist",
                          json=self._movie_payload(tmdb_id),
                          headers=self._headers(), timeout=15)
        r.raise_for_status()

    def remove_movie_watchlist(self, tmdb_id: int) -> None:
        r = requests.post(f"{API}/sync/watchlist/remove",
                          json=self._movie_payload(tmdb_id),
                          headers=self._headers(), timeout=15)
        r.raise_for_status()

    def add_show_watchlist(self, tmdb_id: int) -> None:
        r = requests.post(f"{API}/sync/watchlist",
                          json={"shows": [{"ids": {"tmdb": int(tmdb_id)}}]},
                          headers=self._headers(), timeout=15)
        r.raise_for_status()

    def remove_show_watchlist(self, tmdb_id: int) -> None:
        r = requests.post(f"{API}/sync/watchlist/remove",
                          json={"shows": [{"ids": {"tmdb": int(tmdb_id)}}]},
                          headers=self._headers(), timeout=15)
        r.raise_for_status()

    def watchlist_movies(self) -> list[int]:
        """TMDB ids of every movie on the user's Trakt watchlist."""
        r = requests.get(f"{API}/sync/watchlist/movies",
                         headers=self._headers(), timeout=30)
        r.raise_for_status()
        out: list[int] = []
        for entry in r.json() or []:
            tid = ((entry.get("movie") or {}).get("ids") or {}).get("tmdb")
            if isinstance(tid, int):
                out.append(tid)
        return out

    def watchlist_shows(self) -> list[int]:
        """TMDB ids of every show on the user's Trakt watchlist."""
        r = requests.get(f"{API}/sync/watchlist/shows",
                         headers=self._headers(), timeout=30)
        r.raise_for_status()
        out: list[int] = []
        for entry in r.json() or []:
            tid = ((entry.get("show") or {}).get("ids") or {}).get("tmdb")
            if isinstance(tid, int):
                out.append(tid)
        return out

    # -- favorites (mirrored to a personal Trakt list) -----------------------

    FAV_LIST_NAME = "dopeIPTV Favorites"

    def _ensure_fav_list(self) -> str | None:
        """Return the slug of the personal 'dopeIPTV Favorites' list,
        creating it (and caching the slug) on first use."""
        slug = self.settings.value("trakt_fav_list_slug", "") or ""
        if slug:
            return slug
        try:
            r = requests.get(f"{API}/users/me/lists",
                             headers=self._headers(), timeout=15)
            r.raise_for_status()
            for lst in r.json() or []:
                if lst.get("name") == self.FAV_LIST_NAME:
                    slug = (lst.get("ids") or {}).get("slug") or ""
                    if slug:
                        self.settings.setValue("trakt_fav_list_slug", slug)
                        return slug
            r = requests.post(
                f"{API}/users/me/lists",
                json={"name": self.FAV_LIST_NAME,
                      "description": "Favorites from dopeIPTV.",
                      "privacy": "private"},
                headers=self._headers(), timeout=15)
            r.raise_for_status()
            slug = (r.json().get("ids") or {}).get("slug") or ""
            if slug:
                self.settings.setValue("trakt_fav_list_slug", slug)
            return slug or None
        except Exception:
            return None

    def _fav_items(self, tmdb_id: int, kind: str) -> dict:
        key = "movies" if kind == "vod" else "shows"
        return {key: [{"ids": {"tmdb": int(tmdb_id)}}]}

    def add_favorite(self, tmdb_id: int, kind: str) -> None:
        slug = self._ensure_fav_list()
        if not slug:
            return
        r = requests.post(f"{API}/users/me/lists/{slug}/items",
                          json=self._fav_items(tmdb_id, kind),
                          headers=self._headers(), timeout=15)
        r.raise_for_status()

    def remove_favorite(self, tmdb_id: int, kind: str) -> None:
        slug = self._ensure_fav_list()
        if not slug:
            return
        r = requests.post(f"{API}/users/me/lists/{slug}/items/remove",
                          json=self._fav_items(tmdb_id, kind),
                          headers=self._headers(), timeout=15)
        r.raise_for_status()

    def _fav_list_ids(self, kind: str) -> list[int]:
        slug = self._ensure_fav_list()
        if not slug:
            return []
        endpoint = "movies" if kind == "vod" else "shows"
        node = "movie" if kind == "vod" else "show"
        r = requests.get(f"{API}/users/me/lists/{slug}/items/{endpoint}",
                         headers=self._headers(), timeout=30)
        if r.status_code != 200:
            return []
        out: list[int] = []
        for entry in r.json() or []:
            tid = ((entry.get(node) or {}).get("ids") or {}).get("tmdb")
            if isinstance(tid, int):
                out.append(tid)
        return out

    def favorite_movies(self) -> list[int]:
        return self._fav_list_ids("vod")

    def favorite_shows(self) -> list[int]:
        return self._fav_list_ids("series")

    def watched_shows(self) -> dict[int, list[list[int]]]:
        """Every episode the user has marked watched on any device.
        Returns a mapping show_tmdb_id -> [[season, episode], ...] with each
        watched episode listed once. Trakt returns the full nested seasons
        structure by default; we flatten it so the local WatchedStore can
        answer 'is S03E05 seen?' with a single set lookup."""
        r = requests.get(f"{API}/sync/watched/shows",
                         headers=self._headers(), timeout=45)
        r.raise_for_status()
        out: dict[int, list[list[int]]] = {}
        for entry in r.json() or []:
            tid = ((entry.get("show") or {}).get("ids") or {}).get("tmdb")
            if not isinstance(tid, int):
                continue
            eps: list[list[int]] = []
            for season in entry.get("seasons") or []:
                snum = season.get("number")
                for ep in season.get("episodes") or []:
                    enum = ep.get("number")
                    if isinstance(snum, int) and isinstance(enum, int):
                        eps.append([snum, enum])
            if eps:
                out[tid] = eps
        return out
