"""TMDB metadata lookup: resolves movie/series poster, rating, IMDb id and
cast by title, with a persistent cache so each title is only searched once."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Callable

import requests
from PyQt6.QtCore import QObject, QSettings, QThreadPool, QTimer


from .workers import run_async

# Same env switch as the image loader: DOPEIPTV_IMG_DEBUG=1 traces
# every TMDB title resolution (query used, match / no-match / error)
# to stderr so cover problems can be diagnosed from a user log.
_TMDB_DEBUG = bool(os.environ.get("DOPEIPTV_IMG_DEBUG"))


def _tmdb_dbg(msg: str) -> None:
    if _TMDB_DEBUG:
        print(f"[dopeIPTV:tmdb] {msg}", file=sys.stderr, flush=True)


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

    @staticmethod
    def clean_title(raw: str) -> tuple[str, int | None]:
        """Strip the noise providers wrap around real movie titles so
        the initial TMDB search stands a chance of matching. Handles
        leading language tags (EN|, SV -), quality/codec/audio tags
        (1080p, x265, WEB-DL, MULTI), and the bracketed suffixes
        Xtream operators add for indexing purposes ([IMDB], [MULTI],
        [SUB], plus empty [] or (something) that follow). Returns
        the cleaned title and the year if one appears in the raw
        string, so callers can pre-fill both a search box and a year
        filter. Shared between the auto-matcher (so covers show up
        for provider titles that TMDB would otherwise reject) and
        the manual-match dialog (so the search box lands on a sane
        starting query)."""
        t = raw or ""
        # Extract the year first, from the raw string, so a bracketed
        # or parenthesised year isn't collateral-damage of the
        # bracket-suffix strip a couple of lines down.
        year: int | None = None
        year_m = re.search(r"\b(19|20)\d{2}\b", t)
        if year_m:
            try:
                year = int(year_m.group(0))
            except ValueError:
                year = None
        # Leading 'EN|', 'SV - ', 'FR:' language tag
        t = re.sub(r"^\s*[A-Za-z]{2,3}\s*[|\-:]\s*", "", t)
        # Bracketed suffixes: '[IMDB]', '[MULTI]', '[SUB]', '[HDR]',
        # trailing '[ ]' or '( )' or '(2023)'
        t = re.sub(r"\s*[\[(][^\])]*[\])]\s*$", "", t)
        # Codec / audio / language noise tail
        t = re.sub(
            r"\b(1080p|720p|2160p|4K|UHD|HDR|WEB[-.]?DL|WEB[-.]?RIP|"
            r"BR[-.]?RIP|BluRay|BDR[iI][pP]|x265|x264|HEVC|H\.?264|"
            r"H\.?265|AAC|DTS|AC3|MULTI|VOSTFR|VOST|VF|SUB|DUAL|"
            r"REMUX|EXTENDED|UNRATED|DIRECTORS?[ .]CUT)\b.*$", "",
            t, flags=re.IGNORECASE)
        # Strip any leftover bare year, optionally in parens, so it
        # doesn't confuse the TMDB search string.
        t = re.sub(r"\s*\(?\b(19|20)\d{2}\b\)?\s*", " ", t)
        # Trim leftover punctuation / whitespace
        t = t.strip(" -_.|:;/\t\n")
        t = re.sub(r"\s+", " ", t)
        return t, year

    def _ensure_fetch(self, title: str, kind: str, key: str) -> None:
        if key in self._pending:
            return
        self._pending.add(key)
        # Provider titles carry marketing noise (language prefix,
        # quality tags, '[IMDB]' / '[MULTI]' bracket suffixes, year
        # in parens) that TMDB's search endpoint scores against the
        # real title and rejects. Clean before searching so the
        # auto-match hits the same titles the manual dialog already
        # succeeds on.
        cleaned, year = self.clean_title(title)
        search_query = cleaned or title

        def fetch(t=search_query, k=kind):
            return self.client.fetch_details(t, k)

        def done(details, key=key, raw=title, q=search_query):
            # Never overwrite a manual pick with an auto-search result. This
            # can happen if the cache entry gets evicted and re-fetched.
            existing = self._cache.get(key) or {}
            if existing.get("manual"):
                self._pending.discard(key)
                self._on_resolved(key)
                return
            if details:
                _tmdb_dbg(f"MATCH  q={q!r} raw={raw!r} "
                          f"poster={bool(details.get('poster_url'))}")
            else:
                _tmdb_dbg(f"NOMATCH q={q!r} raw={raw!r}")
            self._cache[key] = details or {}
            self._save()
            self._pending.discard(key)
            self._on_resolved(key)

        def fail(msg, key=key, raw=title, q=search_query):
            # A TMDB request that timed out / hit a 5xx counts as
            # resolved-with-no-match: the delegate can safely fall
            # back to the provider cover from now on and stop
            # painting the placeholder letter. Notify waiting
            # callbacks the same way done() does so the row actually
            # gets a repaint instead of freezing on the placeholder.
            _tmdb_dbg(f"ERROR  q={q!r} raw={raw!r} {str(msg)[:80]}")
            self._cache[key] = {}
            self._pending.discard(key)
            self._on_resolved(key)

        run_async(self.pool, fetch, done, fail)

    def set_manual_match(self, title: str, kind: str, tmdb_id: int,
                         callback: Callable[[dict], None],
                         preview: dict | None = None) -> None:
        """User picked a specific TMDB entry for this title. Cache the
        pick immediately with whatever fields the caller already had
        from search (poster_url, title, year, overview) so the list
        poster + detail panel update on the next paint even if the
        follow-up fetch_details_by_id call fails; then fire that fetch
        in the background to merge in the richer fields (rating,
        cast, imdb id) and re-notify. The manual=True flag survives
        future auto-searches either way."""
        key = self._key(title, kind)

        seed = {"manual": True, "tmdb_id": tmdb_id}
        if preview:
            for k in ("poster_url", "title", "year",
                      "overview", "release_date"):
                v = preview.get(k)
                if v is not None:
                    seed[k] = v
        self._cache[key] = seed
        self._save()
        # Notify the delegate / detail panel right away so the cover
        # can appear before the details-endpoint round-trip returns.
        for cb in self._waiting.pop(key, []):
            try:
                cb(seed)
            except RuntimeError:
                pass
        try:
            callback(seed)
        except RuntimeError:
            pass

        def fetch(tid=tmdb_id, k=kind):
            return self.client.fetch_details_by_id(tid, k)

        def done(details, key=key):
            # Merge the richer TMDB fields on top of the seed. Keep
            # any seed field the details endpoint didn't return
            # (poster_url in particular - the search result's poster
            # is authoritative for the user's pick).
            d = dict(self._cache.get(key) or seed)
            for k, v in (details or {}).items():
                if v is None and d.get(k) is not None:
                    continue
                d[k] = v
            d["manual"] = True
            d["tmdb_id"] = tmdb_id
            self._cache[key] = d
            self._save()
            try:
                callback(d)
            except RuntimeError:
                pass

        def fail(_msg, key=key):
            # Seed is already in the cache; keep the poster visible
            # and don't retry. The delegate + detail panel already
            # got their notification above.
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

    def tmdb_id_for(self, title: str, kind: str) -> int | None:
        """Synchronous TMDB-id lookup used by watched-history badges: only
        returns a value when we already have this title resolved (no
        network side effect), otherwise None. The list delegate calls
        this every paint, so a fetch here would swamp the pool."""
        if not title:
            return None
        cached = self._cache.get(self._key(title, kind))
        if not cached:
            return None
        tid = cached.get("tmdb_id")
        return int(tid) if isinstance(tid, int) else None

    def is_resolved(self, title: str, kind: str) -> bool:
        """True if the TMDB fetch for this title has completed (with or
        without a match); False while the request is still in flight or
        hasn't been kicked yet. Used by the list delegate to decide
        whether to show a provider fallback cover while TMDB fetches,
        or wait for TMDB to answer - the fallback loads a wrong URL
        that immediately gets replaced when TMDB resolves, and that
        flicker reads as a 'double load' during the first pass."""
        if not title:
            return True
        return self._key(title, kind) in self._cache

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
