"""TMDB artwork lookup: resolves movie/series posters by title, with a
persistent cache so each title is only searched once."""

from __future__ import annotations

import json
from typing import Callable

import requests
from PyQt6.QtCore import QObject, QSettings, QThreadPool, QTimer

from .workers import run_async


class TmdbClient:
    """Thin wrapper around the TMDB v3 search endpoints."""

    IMG_BASE = "https://image.tmdb.org/t/p/w342"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def poster_url(self, title: str, kind: str) -> str | None:
        endpoint = "movie" if kind == "vod" else "tv"
        r = requests.get(
            f"https://api.themoviedb.org/3/search/{endpoint}",
            params={"api_key": self.api_key, "query": title,
                    "include_adult": "false"},
            timeout=10)
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        poster_path = results[0].get("poster_path")
        if not poster_path:
            return None
        return f"{self.IMG_BASE}{poster_path}"


class PosterResolver(QObject):
    """Async, cached title -> TMDB poster URL resolver.

    Mirrors LogoLoader's get()/callback pattern: returns the cached URL
    (or None) immediately and, if unresolved, kicks off a background
    search that invokes *callback* once done so the caller can repaint.
    """

    CACHE_KEY = "tmdb_poster_cache"

    def __init__(self, pool: QThreadPool, settings: QSettings,
                 client: TmdbClient) -> None:
        super().__init__()
        self.pool = pool
        self.settings = settings
        self.client = client
        try:
            self._cache: dict[str, str] = json.loads(
                settings.value(self.CACHE_KEY, "") or "{}")
        except Exception:
            self._cache = {}
        self._pending: set[str] = set()
        self._dirty = False
        # A resolved poster writes the whole (growing) cache back to
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

    def get(self, title: str, kind: str,
            callback: Callable[[str | None], None]) -> str | None:
        if not title:
            return None
        key = self._key(title, kind)
        if key in self._cache:
            return self._cache[key] or None
        if key not in self._pending:
            self._pending.add(key)

            def fetch(t=title, k=kind):
                return self.client.poster_url(t, k)

            def done(url, key=key):
                self._cache[key] = url or ""
                self._save()
                self._pending.discard(key)
                callback(url)

            def fail(_msg, key=key):
                self._cache[key] = ""
                self._pending.discard(key)

            run_async(self.pool, fetch, done, fail)
        return None
