"""Trakt.tv integration: device-code auth, scrobbling, watchlist/history."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time

from ..core._lazy_requests import requests
from ..core.log import log
from PyQt6.QtCore import QSettings

API = "https://api.trakt.tv"
# Every OAuth request - the sign-in page, device codes, the code exchange and
# token refresh - goes to this host since Trakt moved its sign-in in 2026.
# The old https://trakt.tv/oauth/authorize lost the request's parameters on
# the way to the new page, so sign-in ended at "invalid_client: client_id is
# required" (developer.trakt.tv, "Authorize Application").
AUTH = "https://auth.trakt.tv"
API_VERSION = "2"

# Loopback redirect for the browser (authorization-code) sign-in. The port is
# fixed so it can be registered as the Trakt app's redirect URI; the callback
# is served by a one-shot local HTTP server (see providers/oauth_loopback.py).
OAUTH_PORT = 41794
REDIRECT_URI = f"http://127.0.0.1:{OAUTH_PORT}/callback"
# The device-code sign-in has no redirect; Trakt's out-of-band URI stands in.
OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
# Refresh this long before the access token runs out.
REFRESH_MARGIN = 600
# After a failed refresh, wait this long before trying again.
REFRESH_BACKOFF = 300

# dopeIPTV's own registered Trakt application, bundled so users get a true
# one-click "Sign in with Trakt" with zero setup. A user who prefers their own
# API app can still override these in Settings (they take precedence). As with
# any distributed native app, an embedded secret is technically extractable;
# that's an accepted trade-off for the frictionless flow.
DEFAULT_CLIENT_ID = (
    "0f954e24983e4dbd2ee5d1bd006a6fe99bbe3ada9a95e47396e5238a9b746ded")
DEFAULT_CLIENT_SECRET = (
    "1bc6756ea9674759f4eb38a50b71d1ee02fb5897af71a961fc9344a52bde4090")


class TraktAuthError(Exception):
    """Raised when Trakt authentication fails, times out, or is declined."""
    pass


def new_pkce_pair() -> tuple[str, str]:
    """A fresh PKCE (verifier, S256 challenge) for one sign-in.

    The verifier is 64 characters from the RFC 7636 alphabet (token_urlsafe
    uses A-Z a-z 0-9 - _), the challenge the base64url SHA-256 of it without
    padding - the only method Trakt accepts."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _oauth_error_code(r) -> str:
    try:
        data = r.json()
    except Exception:
        return ""
    return str(data.get("error") or "") if isinstance(data, dict) else ""


def _oauth_error(r) -> str:
    """': <error> - <description>' from an OAuth error body, or ''."""
    try:
        data = r.json()
    except Exception:
        return ""
    if not isinstance(data, dict) or not data.get("error"):
        return ""
    desc = data.get("error_description")
    return f": {data['error']}" + (f" - {desc}" if desc else "")


class TraktClient:
    """Trakt API client: device auth, token storage, scrobbling, lookups."""

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings
        # A refresh token is single-use: two workers refreshing at once would
        # leave one of them holding a token Trakt has already retired.
        self._refresh_lock = threading.Lock()
        self._refresh_retry_at = 0.0

    # -- credentials / tokens (persisted via QSettings) ---------------------

    @property
    def client_id(self) -> str:
        # A user-supplied app id wins; otherwise fall back to the bundled one.
        return self.settings.value("trakt_client_id", "") or DEFAULT_CLIENT_ID

    @property
    def client_secret(self) -> str:
        """The secret of the app in use, or "" when it has none.

        Optional since Trakt's 2026 sign-in change: apps created from
        October 2026 get no secret at all, and the browser sign-in uses PKCE
        instead. The bundled secret belongs to the bundled app only - sent
        with somebody's own client id it made Trakt reject them."""
        own = self.settings.value("trakt_client_secret", "") or ""
        if own:
            return own
        return DEFAULT_CLIENT_SECRET if self.client_id == DEFAULT_CLIENT_ID else ""

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
            f"{AUTH}/oauth/device/code",
            json={"client_id": self.client_id}, timeout=10)
        r.raise_for_status()
        return r.json()

    def poll_device_token(self, device_code: str) -> dict | None:
        """Step 2: poll for the token. None while pending, dict on success.

        Raises TraktAuthError on denial/expiry.
        """
        body = {"code": device_code, "client_id": self.client_id}
        if self.client_secret:
            body["client_secret"] = self.client_secret
        r = requests.post(f"{AUTH}/oauth/device/token", json=body, timeout=10)
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

    # -- browser (authorization-code) OAuth flow -----------------------------

    def authorize_url(self, state: str, code_challenge: str,
                      redirect_uri: str = REDIRECT_URI) -> str:
        """The Trakt page to open in the browser. If the user is already
        signed in on the web they only click 'Yes' (or are bounced straight
        through if they've authorised the app before) - no password typing.

        *code_challenge* is the PKCE challenge from new_pkce_pair(); the
        matching verifier goes to exchange_code()."""
        from urllib.parse import urlencode
        q = urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })
        return f"{AUTH}/oauth/authorize?{q}"

    def exchange_code(self, code: str, code_verifier: str,
                      redirect_uri: str = REDIRECT_URI) -> dict:
        """Trade the authorization code from the redirect for access/refresh
        tokens and store them. Raises TraktAuthError on failure.

        PKCE: the verifier proves this is the app that asked, and replaces
        the client secret, which Trakt says a desktop app must not send."""
        r = requests.post(
            f"{AUTH}/oauth/token",
            json={"code": code, "client_id": self.client_id,
                  "redirect_uri": redirect_uri,
                  "grant_type": "authorization_code",
                  "code_verifier": code_verifier},
            timeout=15)
        if r.status_code in (200, 201):
            data = r.json()
            self._store_tokens(data)
            return data
        raise TraktAuthError(
            f"Trakt rejected the sign-in (HTTP {r.status_code}"
            f"{_oauth_error(r)}).")

    # -- token refresh --------------------------------------------------------

    def _expires_at(self) -> int:
        try:
            return int(self.settings.value("trakt_expires_at", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _token_due(self) -> bool:
        exp = self._expires_at()
        return bool(self.refresh_token and exp
                    and time.time() >= exp - REFRESH_MARGIN)

    def refresh_tokens(self) -> bool:
        """Trade the refresh token for a new access token and store both.

        Without this the app never renewed anything: the stored refresh
        token and expiry went unread, and when the access token ran out
        every Trakt call failed quietly until the user signed in again."""
        tried: list[str] = []
        grants_refused = 0
        for uri in (REDIRECT_URI, OOB_REDIRECT_URI):
            body = {"refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "redirect_uri": uri,
                    "grant_type": "refresh_token"}
            if self.client_secret:
                body["client_secret"] = self.client_secret
            try:
                r = requests.post(f"{AUTH}/oauth/token", json=body,
                                  timeout=15)
            except requests.RequestException as e:
                log.warning("trakt: token refresh failed - %s",
                            type(e).__name__)
                self._refresh_retry_at = time.monotonic() + REFRESH_BACKOFF
                return False
            if r.status_code in (200, 201):
                self._store_tokens(r.json())
                log.info("trakt: access token refreshed")
                return True
            tried.append(f"{r.status_code}{_oauth_error(r)}")
            if r.status_code == 400 and _oauth_error_code(r) == "invalid_grant":
                grants_refused += 1
        if grants_refused == 2:
            # Trakt's 2026 sign-in move retired every refresh token issued
            # before it ("invalid_grant: session not found"). Keeping the dead
            # token only made every Trakt call fail quietly while Settings
            # still said "Connected"; dropping it says what is true and puts
            # the Connect button back.
            log.warning("trakt: the saved sign-in can no longer be renewed "
                        "(%s) - connect to Trakt again", "; ".join(tried))
            self.disconnect()
            return False
        log.warning("trakt: token refresh refused (HTTP %s) - sign in to "
                    "Trakt again", "/".join(tried))
        self._refresh_retry_at = time.monotonic() + REFRESH_BACKOFF
        return False

    def _ensure_fresh_token(self) -> None:
        if not self._token_due():
            return
        with self._refresh_lock:
            if (not self._token_due()
                    or time.monotonic() < self._refresh_retry_at):
                return
            self.refresh_tokens()

    # -- headers --------------------------------------------------------------

    def _headers(self) -> dict:
        self._ensure_fresh_token()
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

    def watched_movies(self, titles: dict[int, str] | None = None) -> list[int]:
        """Every movie the user has marked watched on any device.
        Returns the list of TMDB ids - dopeIPTV keys its local titles on
        TMDB via the poster-resolver, so imdb ids aren't needed. If a
        *titles* dict is passed it's filled with tmdb id -> 'Title (year)',
        so the Watched list can name rows even without a TMDB key."""
        r = requests.get(f"{API}/sync/watched/movies",
                         headers=self._headers(), timeout=30)
        r.raise_for_status()
        out: list[int] = []
        for entry in r.json() or []:
            movie = entry.get("movie") or {}
            tid = (movie.get("ids") or {}).get("tmdb")
            if isinstance(tid, int):
                out.append(tid)
                if titles is not None:
                    name, year = movie.get("title"), movie.get("year")
                    if name:
                        titles[tid] = f"{name} ({year})" if year else name
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

    def watched_shows(self, titles: dict[int, str] | None = None
                      ) -> dict[int, list[list[int]]]:
        """Every episode the user has marked watched on any device.
        Returns a mapping show_tmdb_id -> [[season, episode], ...] with each
        watched episode listed once. Trakt returns the full nested seasons
        structure by default; we flatten it so the local WatchedStore can
        answer 'is S03E05 seen?' with a single set lookup. If a *titles* dict
        is passed it's filled with show tmdb id -> 'Title (year)'."""
        r = requests.get(f"{API}/sync/watched/shows",
                         headers=self._headers(), timeout=45)
        r.raise_for_status()
        out: dict[int, list[list[int]]] = {}
        for entry in r.json() or []:
            show = entry.get("show") or {}
            tid = (show.get("ids") or {}).get("tmdb")
            if not isinstance(tid, int):
                continue
            if titles is not None:
                name, year = show.get("title"), show.get("year")
                if name:
                    titles[tid] = f"{name} ({year})" if year else name
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
