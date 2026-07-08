"""TMDB metadata lookup: resolves movie/series poster, rating, IMDb id and
cast by title, with a persistent cache so each title is only searched once."""

from __future__ import annotations

import json
from typing import Callable

import requests
from PyQt6.QtCore import QObject, QSettings, QThreadPool, QTimer


from .workers import run_async


class TmdbClient:
    """Thin wrapper around the TMDB v3 search/details endpoints."""

    # w500 poster: sharp enough to display at a much larger size than the
    # old w342 without visibly upscaling/blurring in the detail panel.
    IMG_BASE = "https://image.tmdb.org/t/p/w500"
    PROFILE_IMG_BASE = "https://image.tmdb.org/t/p/w185"
    BASE = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def poster_url(self, title: str, kind: str) -> str | None:
        return (self.fetch_details(title, kind) or {}).get("poster_url")

    def fetch_details(self, title: str, kind: str) -> dict | None:
        """Search by title, then fetch poster/rating/IMDb id/cast in one
        combined request (append_to_response) for the top match."""
        endpoint = "movie" if kind == "vod" else "tv"
        r = requests.get(
            f"{self.BASE}/search/{endpoint}",
            params={"api_key": self.api_key, "query": title,
                    "include_adult": "false"},
            timeout=10)
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        tid = results[0].get("id")
        if tid is None:
            return None
        r2 = requests.get(
            f"{self.BASE}/{endpoint}/{tid}",
            params={"api_key": self.api_key,
                    "append_to_response": "external_ids,credits"},
            timeout=10)
        r2.raise_for_status()
        d = r2.json()
        poster_path = d.get("poster_path") or results[0].get("poster_path")
        cast = []
        for c in (d.get("credits") or {}).get("cast") or []:
            name = c.get("name")
            if not name:
                continue
            profile_path = c.get("profile_path")
            cast.append({
                "name": name,
                "person_id": c.get("id"),
                "profile_url": (f"{self.PROFILE_IMG_BASE}{profile_path}"
                                if profile_path else None),
            })
            if len(cast) == 8:
                break
        genres = ", ".join(g.get("name", "") for g in d.get("genres") or []
                          if g.get("name"))
        release = (d.get("release_date") or d.get("first_air_date") or "")
        return {
            "poster_url": f"{self.IMG_BASE}{poster_path}" if poster_path else None,
            "rating": d.get("vote_average") or None,
            "imdb_id": (d.get("external_ids") or {}).get("imdb_id"),
            "cast": cast,
            "overview": d.get("overview") or "",
            "genres": genres,
            "release_date": release,
        }

    def person_credits(self, person_id: int) -> list[str]:
        """Titles (movies + TV shows) a person has appeared in."""
        r = requests.get(
            f"{self.BASE}/person/{person_id}/combined_credits",
            params={"api_key": self.api_key}, timeout=10)
        r.raise_for_status()
        cast = r.json().get("cast") or []
        titles = [c.get("title") or c.get("name") for c in cast]
        return [t for t in titles if t]

    def search(self, title: str, kind: str,
               year: int | None = None) -> list[dict]:
        """Return up to 12 candidate matches for a title. Used by the manual
        'Match on TMDB...' dialog so the user can pick the correct one when
        auto-match fails (dirty provider titles, ambiguous names, ...)."""
        endpoint = "movie" if kind == "vod" else "tv"
        params = {"api_key": self.api_key, "query": title,
                  "include_adult": "false"}
        if year is not None:
            params["year" if endpoint == "movie" else "first_air_date_year"] = year
        r = requests.get(f"{self.BASE}/search/{endpoint}", params=params,
                         timeout=10)
        r.raise_for_status()
        results = (r.json().get("results") or [])[:12]
        out: list[dict] = []
        for it in results:
            release = (it.get("release_date") or it.get("first_air_date") or "")
            year_str = release[:4] if release else ""
            poster = it.get("poster_path")
            out.append({
                "tmdb_id": it.get("id"),
                "title": it.get("title") or it.get("name") or "?",
                "year": year_str,
                "overview": it.get("overview") or "",
                "poster_url": (f"{self.IMG_BASE}{poster}"
                               if poster else None),
                "vote": it.get("vote_average") or None,
            })
        return out

    def fetch_details_by_id(self, tmdb_id: int, kind: str) -> dict | None:
        """Same shape as fetch_details() but skips the search step - used
        by the manual-match flow after the user has picked an id."""
        endpoint = "movie" if kind == "vod" else "tv"
        r = requests.get(
            f"{self.BASE}/{endpoint}/{tmdb_id}",
            params={"api_key": self.api_key,
                    "append_to_response": "external_ids,credits"},
            timeout=10)
        r.raise_for_status()
        d = r.json()
        poster_path = d.get("poster_path")
        cast = []
        for c in (d.get("credits") or {}).get("cast") or []:
            name = c.get("name")
            if not name:
                continue
            profile_path = c.get("profile_path")
            cast.append({
                "name": name,
                "person_id": c.get("id"),
                "profile_url": (f"{self.PROFILE_IMG_BASE}{profile_path}"
                                if profile_path else None),
            })
            if len(cast) == 8:
                break
        genres = ", ".join(g.get("name", "") for g in d.get("genres") or []
                          if g.get("name"))
        release = (d.get("release_date") or d.get("first_air_date") or "")
        return {
            "poster_url": f"{self.IMG_BASE}{poster_path}" if poster_path else None,
            "rating": d.get("vote_average") or None,
            "imdb_id": (d.get("external_ids") or {}).get("imdb_id"),
            "cast": cast,
            "overview": d.get("overview") or "",
            "genres": genres,
            "release_date": release,
        }

    def search_person(self, name: str) -> int | None:
        """Resolve a person's name to their TMDB id (top match)."""
        r = requests.get(
            f"{self.BASE}/search/person",
            params={"api_key": self.api_key, "query": name,
                    "include_adult": "false"},
            timeout=10)
        r.raise_for_status()
        results = r.json().get("results") or []
        return results[0].get("id") if results else None


class PosterResolver(QObject):
    """Async, cached title -> TMDB metadata resolver.

    Mirrors LogoLoader's get()/callback pattern: returns the cached value
    (or None) immediately and, if unresolved, kicks off a background
    search that invokes *callback* once done so the caller can repaint.
    """

    CACHE_KEY = "tmdb_poster_cache_v3"
    PERSON_CACHE_KEY = "tmdb_person_cache"
    PERSON_ID_CACHE_KEY = "tmdb_person_id_cache"

    def __init__(self, pool: QThreadPool, settings: QSettings,
                 client: TmdbClient) -> None:
        super().__init__()
        self.pool = pool
        self.settings = settings
        self.client = client
        try:
            raw: dict = json.loads(settings.value(self.CACHE_KEY, "") or "{}")
        except Exception:
            raw = {}
        # Migrate the older cache format (plain poster-URL strings) to the
        # richer dict format transparently.
        self._cache: dict[str, dict] = {
            k: (v if isinstance(v, dict) else {"poster_url": v or None})
            for k, v in raw.items()
        }
        self._pending: set[str] = set()
        self._waiting: dict[str, list[Callable]] = {}
        self._dirty = False
        try:
            self._person_cache: dict[str, list[str]] = json.loads(
                settings.value(self.PERSON_CACHE_KEY, "") or "{}")
        except Exception:
            self._person_cache = {}
        self._person_pending: set[str] = set()
        self._person_waiting: dict[str, list[Callable]] = {}
        try:
            self._person_id_cache: dict[str, int | None] = json.loads(
                settings.value(self.PERSON_ID_CACHE_KEY, "") or "{}")
        except Exception:
            self._person_id_cache = {}
        self._person_id_pending: set[str] = set()
        self._person_id_waiting: dict[str, list[Callable]] = {}
        # A resolved entry writes the whole (growing) cache back to
        # QSettings on the main thread. Doing that synchronously on every
        # single completion - which can arrive in bursts when switching
        # categories triggers many lookups at once - is what made the UI
        # stall, so batch writes onto a debounce timer instead.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_save)

    def _save(self) -> None:
        self._dirty = True
        self._save_timer.start(2000)

    def _flush_save(self) -> None:
        if self._dirty:
            self._dirty = False
            self.settings.setValue(self.CACHE_KEY, json.dumps(self._cache))

    def flush(self) -> None:
        """Force any pending cache write out immediately (e.g. on quit)."""
        self._save_timer.stop()
        self._flush_save()

    @staticmethod
    def _key(title: str, kind: str) -> str:
        return f"{kind}:{title.strip().lower()}"

    def _ensure_fetch(self, title: str, kind: str, key: str) -> None:
        if key in self._pending:
            return
        self._pending.add(key)

        def fetch(t=title, k=kind):
            return self.client.fetch_details(t, k)

        def done(details, key=key):
            # Never overwrite a manual pick with an auto-search result. This
            # can happen if the cache entry gets evicted and re-fetched.
            existing = self._cache.get(key) or {}
            if existing.get("manual"):
                self._pending.discard(key)
                self._on_resolved(key)
                return
            self._cache[key] = details or {}
            self._save()
            self._pending.discard(key)
            self._on_resolved(key)

        def fail(_msg, key=key):
            self._cache[key] = {}
            self._pending.discard(key)
            self._waiting.pop(key, None)

        run_async(self.pool, fetch, done, fail)

    def set_manual_match(self, title: str, kind: str, tmdb_id: int,
                         callback: Callable[[dict], None]) -> None:
        """User picked a specific TMDB entry for this title. Fetch its full
        details, cache them with a manual=True flag so they survive future
        auto-searches, and notify the caller (usually the detail panel) so
        the poster + metadata refresh live."""
        key = self._key(title, kind)

        def fetch(tid=tmdb_id, k=kind):
            return self.client.fetch_details_by_id(tid, k)

        def done(details, key=key):
            d = dict(details or {})
            d["manual"] = True
            d["tmdb_id"] = tmdb_id
            self._cache[key] = d
            self._save()
            # Notify anyone waiting on the auto-search too so we don't
            # leave callbacks pending indefinitely.
            for cb in self._waiting.pop(key, []):
                try:
                    cb(d)
                except RuntimeError:
                    pass
            try:
                callback(d)
            except RuntimeError:
                pass

        def fail(_msg):
            pass

        run_async(self.pool, fetch, done, fail)

    def clear_manual_match(self, title: str, kind: str) -> None:
        """Drop a previously-set manual pick so the next lookup goes back
        through the automatic search. Removes the whole cache entry rather
        than just the flag so a fresh auto-search runs."""
        key = self._key(title, kind)
        if key in self._cache:
            del self._cache[key]
            self._save()

    def _on_resolved(self, key: str) -> None:
        for cb in self._waiting.pop(key, []):
            try:
                cb(self._cache.get(key) or {})
            except RuntimeError:
                pass

    def get(self, title: str, kind: str,
            callback: Callable[[str | None], None]) -> str | None:
        """Poster URL only - used by the list/grid delegate."""
        details = self.get_full(title, kind, lambda d: callback(d.get("poster_url")))
        return details.get("poster_url") if details else None

    def get_full(self, title: str, kind: str,
                 callback: Callable[[dict], None]) -> dict | None:
        """Full metadata dict (poster_url/rating/imdb_id/cast), or None
        while the lookup is still pending."""
        if not title:
            return None
        key = self._key(title, kind)
        if key in self._cache:
            return self._cache[key]
        self._waiting.setdefault(key, []).append(callback)
        self._ensure_fetch(title, kind, key)
        return None

    def get_person_credits(
            self, person_id, callback: Callable[[list[str]], None]
    ) -> list[str] | None:
        """Titles a person has appeared in (movies + TV), cached by id."""
        if not person_id:
            return None
        key = str(person_id)
        if key in self._person_cache:
            return self._person_cache[key]
        self._person_waiting.setdefault(key, []).append(callback)
        if key in self._person_pending:
            return None
        self._person_pending.add(key)

        def fetch(pid=person_id):
            return self.client.person_credits(pid)

        def done(titles, key=key):
            self._person_cache[key] = titles or []
            self.settings.setValue(
                self.PERSON_CACHE_KEY, json.dumps(self._person_cache))
            self._person_pending.discard(key)
            for cb in self._person_waiting.pop(key, []):
                try:
                    cb(self._person_cache[key])
                except RuntimeError:
                    pass

        def fail(_msg, key=key):
            self._person_cache[key] = []
            self._person_pending.discard(key)
            self._person_waiting.pop(key, None)

        run_async(self.pool, fetch, done, fail)
        return None

    def resolve_person_id(
            self, name: str, callback: Callable[[int | None], None]
    ) -> int | str | None:
        """Resolve a person's name to a TMDB id (cached), for cast names
        that came from the provider's plain-text list rather than TMDB."""
        if not name:
            return None
        key = f"name:{name.strip().lower()}"
        if key in self._person_id_cache:
            return self._person_id_cache[key]
        self._person_id_waiting.setdefault(key, []).append(callback)
        if key in self._person_id_pending:
            return None
        self._person_id_pending.add(key)

        def fetch(n=name):
            return self.client.search_person(n)

        def done(pid, key=key):
            self._person_id_cache[key] = pid
            self.settings.setValue(
                self.PERSON_ID_CACHE_KEY, json.dumps(self._person_id_cache))
            self._person_id_pending.discard(key)
            for cb in self._person_id_waiting.pop(key, []):
                try:
                    cb(pid)
                except RuntimeError:
                    pass

        def fail(_msg, key=key):
            self._person_id_pending.discard(key)
            self._person_id_waiting.pop(key, None)

        run_async(self.pool, fetch, done, fail)
        return None
