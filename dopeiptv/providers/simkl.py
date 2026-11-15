"""Simkl (simkl.com): sign-in with a PIN, scrobbling, watched marks and the
watchlist - the same jobs Trakt does here, for people who keep their lists
on Simkl instead (or on both).

Every request names the app three ways, as Simkl asks: its client id, an
app-name and an app-version (URL parameters), and a User-Agent. The client
id is dopeIPTV's own Simkl application (AUTH V2, "TV, devices & command
line": a client id and no secret), baked into release builds from a CI
secret exactly like the TMDB key (bundled_client_id); while there is none,
Simkl is not offered at all.

Sign-in is AUTH V2's device flow (RFC 8628, api.simkl.org's "Device / PIN
flow"): POST /oauth2/device gives a code the person approves at
simkl.com/pin, and /oauth2/token is asked until they have. That gives an
access token for 7 days and a refresh token for 180 days that slides on
every use (non-rotating); the access token is renewed shortly before it runs
out, or once on a 401. Signing out revokes the grant. The tokens live in the
"simkl_access_token", "simkl_refresh_token" and "simkl_token_expires"
settings - never logged, never synced.

Titles are found by their TMDB id (what the app resolves its posters with),
with the title and year beside it: Simkl takes the first id it knows and
falls back to the title.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping
from typing import Any, Protocol

from .. import VERSION
from ..core._lazy_requests import requests
from ..core.log import log
from ..i18n import LocalizedError

API = "https://api.simkl.com"
#: Where a person types the PIN, when Simkl does not say.
PIN_PAGE = "https://simkl.com/pin/"
APP_NAME = "dopeiptv"
TOKEN_KEY = "simkl_access_token"
REFRESH_KEY = "simkl_refresh_token"
#: When the access token stops being accepted (unix seconds; 0: not known).
EXPIRES_KEY = "simkl_token_expires"
#: Scrobbles and marks write; without media:write the token is read-only.
SCOPE = "media:read media:write"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
#: Renewed this long before it runs out (it lasts 7 days).
REFRESH_EARLY_S = 6 * 3600
UA = f"dopeIPTV/{VERSION}"
#: The four lists read in one sync come from one answer, this long.
ALL_ITEMS_TTL = 60.0


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name

    def remove(self, key: str) -> None: ...


class SimklAuthError(LocalizedError):
    """The PIN sign-in failed, ran out or was declined - with a string key,
    or with Simkl's own words."""


_baked: str | None = None
#: dopeIPTV's own AUTH V2 app ("TV, devices & command line"). Simkl: "Client
#: IDs are public and safe on GitHub for every type" - no secret goes with
#: it. Empty: a release build's baked id, else Simkl is not offered.
PUBLIC_CLIENT_ID = "ec150d64a2e6f124e2408ac81e9a6aa120c3cc15a3830c44bf300b092982e3e1"


def bundled_client_id() -> str:
    """dopeIPTV's Simkl client id: DOPEIPTV_SIMKL_CLIENT_ID for local
    development, else PUBLIC_CLIENT_ID, else the git-ignored
    dopeiptv/_simkl_key.py release builds get from the SIMKL_CLIENT_ID
    secret ("Bake built-in Simkl client id" in .github/workflows/release.yml).
    The file is looked for once: a missing module is not remembered by
    Python, and this is asked whenever the menus want to know who is signed
    in."""
    global _baked
    key = (os.environ.get("DOPEIPTV_SIMKL_CLIENT_ID") or "").strip()
    if key:
        return key
    if PUBLIC_CLIENT_ID:
        return PUBLIC_CLIENT_ID
    if _baked is None:
        try:
            from .._simkl_key import SIMKL_CLIENT_ID  # type: ignore
            _baked = (SIMKL_CLIENT_ID or "").strip()
        except Exception:
            _baked = ""
    return _baked


# -- what goes over the wire -----------------------------------------------------

def _int(value: Any) -> int | None:
    """An id as Simkl sends it: a number, or a number in a string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def media(tmdb_id: int | None = None, title: str = "",
          year: int | None = None) -> dict[str, Any]:
    """A movie or show as Simkl finds it: its TMDB id, its title, its year."""
    out: dict[str, Any] = {}
    if title:
        out["title"] = title
    if year:
        out["year"] = int(year)
    if isinstance(tmdb_id, int):
        out["ids"] = {"tmdb": int(tmdb_id)}
    return out


def scrobble_movie(tmdb_id: int | None, title: str, year: int | None = None) -> dict[str, Any]:
    return {"movie": media(tmdb_id, title, year)}


def scrobble_episode(show_tmdb_id: int | None, show_title: str, season: int,
                     episode: int) -> dict[str, Any]:
    return {"show": media(show_tmdb_id, show_title),
            "episode": {"season": int(season), "number": int(episode)}}


def movies_body(tmdb_id: int, to: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"ids": {"tmdb": int(tmdb_id)}}
    if to:
        entry["to"] = to
    return {"movies": [entry]}


def shows_body(tmdb_id: int, to: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"ids": {"tmdb": int(tmdb_id)}}
    if to:
        entry["to"] = to
    return {"shows": [entry]}


def episode_body(show_tmdb_id: int, season: int, episode: int) -> dict[str, Any]:
    return {"shows": [{"ids": {"tmdb": int(show_tmdb_id)},
                       "seasons": [{"number": int(season),
                                    "episodes": [{"number": int(episode)}]}]}]}


def _entries(data: Any, key: str) -> list[dict[str, Any]]:
    got = data.get(key) if isinstance(data, dict) else None
    return [e for e in got if isinstance(e, dict)] if isinstance(got, list) else []


def _node(entry: Mapping[str, Any]) -> dict[str, Any]:
    for k in ("movie", "show", "anime"):
        v = entry.get(k)
        if isinstance(v, dict):
            return v
    return {}


def _tmdb(entry: Mapping[str, Any]) -> int | None:
    return _int((_node(entry).get("ids") or {}).get("tmdb"))


def _name(entry: Mapping[str, Any]) -> str:
    node = _node(entry)
    name, year = str(node.get("title") or ""), node.get("year")
    return f"{name} ({year})" if name and year else name


def watched_movies(data: Any, titles: dict[int, str] | None = None) -> list[int]:
    """TMDB ids of the movies Simkl has as watched (completed)."""
    out: list[int] = []
    for e in _entries(data, "movies"):
        tid = _tmdb(e)
        if tid is None or e.get("status") != "completed":
            continue
        out.append(tid)
        if titles is not None and _name(e):
            titles[tid] = _name(e)
    return out


def watched_shows(data: Any, titles: dict[int, str] | None = None
                  ) -> dict[int, list[list[int]]]:
    """show TMDB id -> [[season, episode], ...] of every episode Simkl has
    as watched: one with a watched time, or any of a completed show."""
    out: dict[int, list[list[int]]] = {}
    for e in _entries(data, "shows") + _entries(data, "anime"):
        tid = _tmdb(e)
        if tid is None or e.get("status") == "plantowatch":
            continue
        done = e.get("status") == "completed"
        eps: list[list[int]] = []
        for season in e.get("seasons") or []:
            snum = _int((season or {}).get("number"))
            for ep in (season or {}).get("episodes") or []:
                enum = _int((ep or {}).get("number"))
                if snum is None or enum is None:
                    continue
                if done or (ep or {}).get("watched_at"):
                    eps.append([snum, enum])
        if eps:
            have = out.setdefault(tid, [])
            have += [p for p in eps if p not in have]
            if titles is not None and _name(e):
                titles[tid] = _name(e)
    return out


def plan_to_watch(data: Any, key: str) -> list[int]:
    """TMDB ids on the "Plan to watch" list: *key* "movies" or "shows"
    (shows and anime together)."""
    keys = ("movies",) if key == "movies" else ("shows", "anime")
    out: list[int] = []
    for k in keys:
        for e in _entries(data, k):
            tid = _tmdb(e)
            if tid is not None and e.get("status") == "plantowatch" and tid not in out:
                out.append(tid)
    return out


def listing(data: Any, status: str | None) -> list[dict[str, Any]]:
    """Rows for the watchlist and history window: {"kind", "title",
    "watched_at"}, newest first. *status* "plantowatch" for the watchlist,
    None for everything watched."""
    rows: list[dict[str, Any]] = []
    for key, kind in (("movies", "movie"), ("shows", "show"), ("anime", "show")):
        for e in _entries(data, key):
            st = e.get("status")
            if status is not None and st != status:
                continue
            when = str(e.get("last_watched_at") or "")
            if status is None and (st == "plantowatch" or not when):
                continue
            if _name(e):
                rows.append({"kind": kind, "title": _name(e), "watched_at": when,
                             "added_at": str(e.get("added_to_watchlist_at") or "")})
    rows.sort(key=lambda r: r["watched_at"] or r["added_at"], reverse=True)
    return rows


class SimklClient:
    """Simkl's API for one account: the PIN sign-in, scrobbles, marks."""

    def __init__(self, settings: Settings, session: Any = None,
                 client_id: str | None = None) -> None:
        self.settings = settings
        self.session = session
        self._client_id = client_id
        self._items: tuple[float, Any] | None = None
        self._lock = threading.Lock()
        self._token_lock = threading.Lock()
        #: user_code -> device_code for a sign-in under way (the device code
        #: is what is polled with; never shown, never logged).
        self._device: dict[str, str] = {}

    # -- the account ----------------------------------------------------------

    @property
    def client_id(self) -> str:
        return self._client_id if self._client_id is not None else bundled_client_id()

    def available(self) -> bool:
        """Whether this build can offer Simkl at all (it has a client id)."""
        return bool(self.client_id)

    @property
    def access_token(self) -> str:
        return str(self.settings.value(TOKEN_KEY, "") or "")

    def is_connected(self) -> bool:
        return bool(self.available() and self.access_token)

    def disconnect(self) -> None:
        """Signed out here, and the grant revoked at Simkl (on a thread of
        its own, best effort: revoke always answers 200)."""
        token = str(self.settings.value(REFRESH_KEY, "") or "") or self.access_token
        self._forget()
        if token and self.client_id:
            threading.Thread(target=self._revoke, args=(token,), daemon=True,
                             name="simkl-revoke").start()

    def _forget(self) -> None:
        for key in (TOKEN_KEY, REFRESH_KEY, EXPIRES_KEY):
            self.settings.remove(key)
        self._items = None

    def _revoke(self, token: str) -> None:
        try:
            self._http().post(f"{API}/oauth2/revoke", data={
                "client_id": self.client_id, "token": token},
                headers=self._form_headers(), timeout=10)
        except Exception as e:  # noqa: BLE001 - signing out never fails here
            log.debug("simkl: revoke not sent (%s)", type(e).__name__)

    # -- the tokens -------------------------------------------------------------

    def _store(self, data: Mapping[str, Any]) -> bool:
        """An /oauth2/token answer kept. False when it carried no token."""
        token = str(data.get("access_token") or "")
        if not token:
            return False
        self.settings.setValue(TOKEN_KEY, token)
        refresh = str(data.get("refresh_token") or "")
        if refresh:
            self.settings.setValue(REFRESH_KEY, refresh)
        try:
            life = int(data.get("expires_in") or 0)
        except (TypeError, ValueError):
            life = 0
        self.settings.setValue(EXPIRES_KEY, int(time.time()) + life if life > 0 else 0)
        scope = str(data.get("scope") or "")
        if scope and "media:write" not in scope.split():
            # An unknown scope is downgraded to read-only, never refused.
            log.warning("simkl: the sign-in is read-only (scope %r)", scope)
        self._items = None
        return True

    def _due(self) -> bool:
        try:
            until = int(self.settings.value(EXPIRES_KEY, 0) or 0)
        except (TypeError, ValueError):
            until = 0
        return bool(until) and time.time() > until - REFRESH_EARLY_S

    def refresh(self) -> bool:
        """A new access token from the refresh token. False when there is
        none, or Simkl refused it - then the sign-in is gone (revoked from
        Simkl's Connected Apps, or 180 days unused) and is forgotten here."""
        with self._token_lock:
            rt = str(self.settings.value(REFRESH_KEY, "") or "")
            if not rt or not self.client_id:
                return False
            try:
                r = self._http().post(f"{API}/oauth2/token", data={
                    "grant_type": "refresh_token", "client_id": self.client_id,
                    "refresh_token": rt}, headers=self._form_headers(), timeout=15)
            except Exception as e:  # noqa: BLE001 - offline: try again later
                log.info("simkl: could not renew the sign-in (%s)", type(e).__name__)
                return False
            if r.status_code == 200 and self._store(r.json() or {}):
                log.info("simkl: sign-in renewed")
                return True
            if r.status_code in (400, 401):
                log.warning("simkl: the sign-in was not renewed - connect again")
                self._forget()
            return False

    def _fresh(self) -> None:
        if self.access_token and self._due():
            self.refresh()

    # -- requests -------------------------------------------------------------

    def params(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"client_id": self.client_id, "app-name": APP_NAME,
                               "app-version": VERSION}
        out.update(extra or {})
        return out

    def _form_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": UA}

    def headers(self, auth: bool = True) -> dict[str, str]:
        h = {"Content-Type": "application/json", "simkl-api-key": self.client_id,
             "User-Agent": UA}
        if auth and self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h

    def _http(self) -> Any:
        return self.session or requests

    def _get(self, path: str, extra: Mapping[str, Any] | None = None,
             auth: bool = True, timeout: float = 15) -> Any:
        if auth:
            self._fresh()
        r = self._http().get(f"{API}{path}", params=self.params(extra),
                             headers=self.headers(auth), timeout=timeout)
        if auth and r.status_code == 401 and self.refresh():
            r = self._http().get(f"{API}{path}", params=self.params(extra),
                                 headers=self.headers(auth), timeout=timeout)
        return r

    def _post(self, path: str, body: Mapping[str, Any], timeout: float = 15) -> Any:
        self._fresh()
        r = self._http().post(f"{API}{path}", params=self.params(), json=dict(body),
                              headers=self.headers(), timeout=timeout)
        if r.status_code == 401 and self.refresh():
            r = self._http().post(f"{API}{path}", params=self.params(), json=dict(body),
                                  headers=self.headers(), timeout=timeout)
        self._items = None              # what changed is read again
        return r

    def _ok(self, r: Any) -> None:
        if r.status_code == 401:
            log.warning("simkl: the sign-in is no longer accepted - connect again")
        r.raise_for_status()

    # -- the PIN sign-in (AUTH V2 device flow) ---------------------------------

    def start_pin(self) -> dict[str, Any]:
        """Step 1: a code for the person to approve at simkl.com/pin.
        {"user_code", "verification_url", "verification_uri_complete",
        "expires_in", "interval"} - the device code stays here."""
        r = self._http().post(f"{API}/oauth2/device", data={
            "client_id": self.client_id, "scope": SCOPE},
            headers=self._form_headers(), timeout=10)
        data = r.json() if r.content else {}
        if r.status_code == 401:
            # A V1 app's client id: this flow is V2's alone.
            raise SimklAuthError(str((data or {}).get("error_description")
                                     or "invalid_client")[:200])
        r.raise_for_status()
        if not isinstance(data, dict) or not data.get("user_code") \
                or not data.get("device_code"):
            raise SimklAuthError("simkl_could_not_start", msg="no code")
        code = str(data["user_code"])
        self._device = {code: str(data.pop("device_code"))}
        data.setdefault("verification_url", data.get("verification_uri") or PIN_PAGE)
        return data

    def poll_pin(self, user_code: str) -> dict[str, Any] | None:
        """Step 2: None while the code waits (Simkl sends no "declined" -
        the window keeps its own deadline); the answer once it is approved
        (the tokens are stored). SimklAuthError when it ran out or failed."""
        device = self._device.get(user_code)
        if not device:
            raise SimklAuthError("trakt_code_expired")
        r = self._http().post(f"{API}/oauth2/token", data={
            "grant_type": DEVICE_GRANT, "client_id": self.client_id,
            "device_code": device}, headers=self._form_headers(), timeout=10)
        try:
            data = r.json() or {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if r.status_code == 200 and self._store(data):
            self._device.pop(user_code, None)
            log.info("simkl: signed in")
            return data
        error = str(data.get("error") or "")
        if error == "authorization_pending":
            return None
        if error == "slow_down" or r.status_code == 429:
            # Wait before the next try: an early poll re-arms the window.
            time.sleep(5)
            return None
        if error == "expired_token":
            self._device.pop(user_code, None)
            raise SimklAuthError("trakt_code_expired")
        self._device.pop(user_code, None)
        raise SimklAuthError(str(data.get("error_description") or error
                                 or f"HTTP {r.status_code}")[:200])

    # -- scrobbling -------------------------------------------------------------

    def _scrobble(self, action: str, payload: Mapping[str, Any], progress: float) -> None:
        body = dict(payload)
        body["progress"] = round(max(0.0, min(100.0, float(progress))), 2)
        r = self._post(f"/scrobble/{action}", body, timeout=10)
        if r.status_code not in (200, 201, 409):
            log.info("simkl: scrobble %s answered HTTP %s", action, r.status_code)

    def scrobble_start(self, payload: Mapping[str, Any], progress: float = 0.0) -> None:
        self._scrobble("start", payload, progress)

    def scrobble_pause(self, payload: Mapping[str, Any], progress: float = 0.0) -> None:
        self._scrobble("pause", payload, progress)

    def scrobble_stop(self, payload: Mapping[str, Any], progress: float = 0.0) -> None:
        """At 80 % or more Simkl marks it watched; below that it keeps the
        place, as a pause."""
        self._scrobble("stop", payload, progress)

    # -- watched marks ------------------------------------------------------------

    def add_movie_history(self, tmdb_id: int) -> None:
        self._ok(self._post("/sync/history", movies_body(tmdb_id)))

    def remove_movie_history(self, tmdb_id: int) -> None:
        self._ok(self._post("/sync/history/remove", movies_body(tmdb_id)))

    def add_show_history(self, show_tmdb_id: int) -> None:
        self._ok(self._post("/sync/history", shows_body(show_tmdb_id)))

    def remove_show_history(self, show_tmdb_id: int) -> None:
        self._ok(self._post("/sync/history/remove", shows_body(show_tmdb_id)))

    def add_episode_history(self, show_tmdb_id: int, season: int, episode: int) -> None:
        self._ok(self._post("/sync/history", episode_body(show_tmdb_id, season, episode)))

    def remove_episode_history(self, show_tmdb_id: int, season: int, episode: int) -> None:
        self._ok(self._post("/sync/history/remove",
                            episode_body(show_tmdb_id, season, episode)))

    # -- the watchlist ("Plan to watch") -----------------------------------------

    def add_movie_watchlist(self, tmdb_id: int) -> None:
        self._ok(self._post("/sync/add-to-list", movies_body(tmdb_id, "plantowatch")))

    def add_show_watchlist(self, tmdb_id: int) -> None:
        self._ok(self._post("/sync/add-to-list", shows_body(tmdb_id, "plantowatch")))

    def remove_movie_watchlist(self, tmdb_id: int) -> None:
        # Simkl takes a title off a list by taking it off the account: only
        # one that is still just planned, never one with a watch behind it.
        if int(tmdb_id) in self.watchlist_movies(fresh=True):
            self._ok(self._post("/sync/history/remove", movies_body(tmdb_id)))

    def remove_show_watchlist(self, tmdb_id: int) -> None:
        if int(tmdb_id) in self.watchlist_shows(fresh=True):
            self._ok(self._post("/sync/history/remove", shows_body(tmdb_id)))

    # -- what the account holds ---------------------------------------------------

    def all_items(self, fresh: bool = False) -> Any:
        """Everything on the account's lists, with the watched episodes -
        one answer for the four lists a sync reads (ALL_ITEMS_TTL)."""
        with self._lock:
            now = time.monotonic()
            if not fresh and self._items is not None and now - self._items[0] < ALL_ITEMS_TTL:
                return self._items[1]
            r = self._get("/sync/all-items/", {"extended": "full",
                                               "episode_watched_at": "yes",
                                               "include_all_episodes": "yes"},
                          timeout=45)
            self._ok(r)
            data = r.json() if r.content else {}
            data = data if isinstance(data, dict) else {}
            self._items = (now, data)
            return data

    def watched_movies(self, titles: dict[int, str] | None = None) -> list[int]:
        return watched_movies(self.all_items(), titles)

    def watched_shows(self, titles: dict[int, str] | None = None
                      ) -> dict[int, list[list[int]]]:
        return watched_shows(self.all_items(), titles)

    def watchlist_movies(self, fresh: bool = False) -> list[int]:
        return plan_to_watch(self.all_items(fresh), "movies")

    def watchlist_shows(self, fresh: bool = False) -> list[int]:
        return plan_to_watch(self.all_items(fresh), "shows")

    def watchlist(self) -> list[dict[str, Any]]:
        return listing(self.all_items(), "plantowatch")

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return listing(self.all_items(), None)[:limit]
