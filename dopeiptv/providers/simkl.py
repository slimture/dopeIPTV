"""Simkl (simkl.com): sign-in with a PIN, scrobbling, watched marks and the
watchlist - the same jobs Trakt does here, for people who keep their lists
on Simkl instead (or on both).

Every request names the app three ways, as Simkl asks: its client id, an
app-name and an app-version (URL parameters), and a User-Agent. The client
id is dopeIPTV's own Simkl application, baked into release builds from a CI
secret exactly like the TMDB key (bundled_client_id); while there is none,
Simkl is not offered at all. A sign-in is a token that does not run out, in
the "simkl_access_token" setting - never logged, never synced.

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
UA = f"dopeIPTV/{VERSION}"
#: The four lists read in one sync come from one answer, this long.
ALL_ITEMS_TTL = 60.0
#: Simkl's own words while the PIN waits for its person.
_PENDING = ("pending", "authorization pending")


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name

    def remove(self, key: str) -> None: ...


class SimklAuthError(LocalizedError):
    """The PIN sign-in failed, ran out or was declined - with a string key,
    or with Simkl's own words."""


_baked: str | None = None


def bundled_client_id() -> str:
    """dopeIPTV's Simkl client id: DOPEIPTV_SIMKL_CLIENT_ID for local
    development, else the git-ignored dopeiptv/_simkl_key.py release builds
    get from the SIMKL_CLIENT_ID secret ("Bake built-in Simkl client id" in
    .github/workflows/release.yml). Never in the public repository. The
    file is looked for once: a missing module is not remembered by Python,
    and this is asked whenever the menus want to know who is signed in."""
    global _baked
    key = (os.environ.get("DOPEIPTV_SIMKL_CLIENT_ID") or "").strip()
    if key:
        return key
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
        self.settings.remove(TOKEN_KEY)
        self._items = None

    # -- requests -------------------------------------------------------------

    def params(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"client_id": self.client_id, "app-name": APP_NAME,
                               "app-version": VERSION}
        out.update(extra or {})
        return out

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
        return self._http().get(f"{API}{path}", params=self.params(extra),
                                headers=self.headers(auth), timeout=timeout)

    def _post(self, path: str, body: Mapping[str, Any], timeout: float = 15) -> Any:
        r = self._http().post(f"{API}{path}", params=self.params(), json=dict(body),
                              headers=self.headers(), timeout=timeout)
        self._items = None              # what changed is read again
        return r

    def _ok(self, r: Any) -> None:
        if r.status_code == 401:
            log.warning("simkl: the sign-in is no longer accepted - connect again")
        r.raise_for_status()

    # -- the PIN sign-in --------------------------------------------------------

    def start_pin(self) -> dict[str, Any]:
        """Step 1: a code for the person to type at simkl.com/pin.
        {"user_code", "verification_url", "expires_in", "interval"}."""
        r = self._get("/oauth/pin", auth=False, timeout=10)
        r.raise_for_status()
        data = r.json() or {}
        if not isinstance(data, dict) or not data.get("user_code"):
            raise SimklAuthError("simkl_could_not_start", msg=str(data)[:120])
        data.setdefault("verification_url", data.get("verification_uri") or PIN_PAGE)
        return data

    def poll_pin(self, user_code: str) -> dict[str, Any] | None:
        """Step 2: None while the code waits; the answer once it is approved
        (the token is stored). SimklAuthError when it ran out or failed."""
        r = self._get(f"/oauth/pin/{user_code}", auth=False, timeout=10)
        if r.status_code in (404, 410):
            raise SimklAuthError("trakt_code_expired")
        if r.status_code == 429:
            time.sleep(5)
            return None
        r.raise_for_status()
        data = r.json() or {}
        if not isinstance(data, dict):
            return None
        token = str(data.get("access_token") or "")
        if data.get("result") == "OK" and token:
            self.settings.setValue(TOKEN_KEY, token)
            self._items = None
            log.info("simkl: signed in")
            return data
        message = str(data.get("message") or "").strip()
        low = message.lower()
        if not message or low in _PENDING:
            return None
        if "slow" in low:
            time.sleep(5)
            return None
        if "expire" in low:
            raise SimklAuthError("trakt_code_expired")
        raise SimklAuthError(message[:200])

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
