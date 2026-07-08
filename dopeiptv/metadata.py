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
                "profile_url": (f"{self.PROFILE_IMG_BASE}{profile_path}"
                                if profile_path else None),
            })
            if len(cast) == 8:
                break
        return {
            "poster_url": f"{self.IMG_BASE}{poster_path}" if poster_path else None,
            "rating": d.get("vote_average") or None,
            "imdb_id": (d.get("external_ids") or {}).get("imdb_id"),
            "cast": cast,
        }


class PosterResolver(QObject):
    """Async, cached title -> TMDB metadata resolver.

    Mirrors LogoLoader's get()/callback pattern: returns the cached value
    (or None) immediately and, if unresolved, kicks off a background
    search that invokes *callback* once done so the caller can repaint.
    """

    CACHE_KEY = "tmdb_poster_cache_v2"

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
            self._cache[key] = details or {}
            self._save()
            self._pending.discard(key)
            self._on_resolved(key)

        def fail(_msg, key=key):
            self._cache[key] = {}
            self._pending.discard(key)
            self._waiting.pop(key, None)

        run_async(self.pool, fetch, done, fail)

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
