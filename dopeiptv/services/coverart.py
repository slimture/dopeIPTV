"""CoverArtService: choose and resolve list/detail artwork.

Owns the TMDB :class:`PosterResolver` lifecycle and the ordered
cover-candidate logic that used to live on ``MainWindow``. It knows
nothing about widgets: it takes an *on_poster_ready* callback that the UI
wires to a repaint, and a *logos* handle used only to consult its
dead/waiting sets (never to render anything).
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import QThreadPool

from ..core.workers import choose_cover_url, is_tmdb_image_url
from ..providers.metadata import PosterResolver, TmdbClient, bundled_tmdb_key


class CoverArtService:
    def __init__(self, settings, logos,
                 on_poster_ready: Callable[[], None]) -> None:
        self._settings = settings
        self._logos = logos
        self._on_poster_ready = on_poster_ready
        # The active TMDB poster resolver, or None when no key is available.
        # Exposed as ``resolver`` because callers across the UI reach the
        # TMDB client through it (id resolution, badges, detail panel).
        self.resolver: PosterResolver | None = None
        # Whether the *list cover* prefers the TMDB title-search poster over
        # the provider's own image (the resolver still runs either way, to
        # feed Trakt id resolution and the watched badges).
        self.prefer_tmdb = False
        self._pool: QThreadPool | None = None
        self.reload()

    # -- resolver lifecycle --------------------------------------------------

    def reload(self) -> None:
        """(Re)build the resolver from current settings. Call after the user
        changes the metadata source or their TMDB key."""
        if self.resolver:
            self.resolver.flush()
        self.resolver = None
        # The TMDB resolver is created whenever an API key is present -
        # NOT only when TMDB is the chosen cover source. It also powers
        # Trakt id resolution (Trakt's API is tmdb-keyed) and the
        # watched/watchlist badges, which must keep working even for a
        # user who prefers the provider's own artwork. The
        # metadata_source setting only decides whether the *list cover*
        # prefers the TMDB title-search poster or the provider's image.
        # A built-in key ships with release builds so TMDB works with no
        # setup. When one is present TMDB is the default artwork source;
        # otherwise we fall back to the provider's own images. A user who
        # explicitly picks a source in Settings overrides the default.
        bundled = bundled_tmdb_key()
        user_key = (self._settings.value("tmdb_api_key", "") or "").strip()
        # Three explicit sources:
        #   "tmdb"     - the built-in key (default when one ships)
        #   "custom"   - the user's own key
        #   "playlist" - the provider's own artwork
        source = self._settings.value("metadata_source", "") or ""
        if not source:
            source = "tmdb" if bundled else ("custom" if user_key
                                             else "playlist")
        self.prefer_tmdb = source in ("tmdb", "custom")
        if source == "custom":
            key = user_key
        elif source == "tmdb":
            # Prefer the built-in key; fall back to a user key so an
            # older "tmdb" setting keeps working before this split.
            key = bundled or user_key
        else:  # provider artwork - still resolve ids for Trakt/badges
            key = user_key or bundled
        if not key:
            return
        # Dedicated thread pool: TMDB lookups must never compete with
        # the shared pool used for channel/EPG loading, or a burst of
        # poster searches can starve real work and look like a freeze.
        # 6 workers is well under TMDB's 50 req/s limit but keeps a
        # newly-opened category with dozens of unseen titles from
        # taking half a minute to fill in the posters - each row
        # takes two sequential HTTP calls (search + details), so
        # fewer workers turn a 50-row scroll into visible latency.
        pool = QThreadPool()
        pool.setMaxThreadCount(6)
        self._pool = pool
        self.resolver = PosterResolver(pool, self._settings, TmdbClient(key))

    def flush(self) -> None:
        if self.resolver:
            self.resolver.flush()

    # -- artwork decisions ---------------------------------------------------

    def poster_for(self, it: dict, kind: str) -> str | None:
        if not self.resolver or kind not in ("vod", "series"):
            return None
        title = it.get("name") or it.get("title") or ""
        if not title:
            return None
        return self.resolver.get(
            title, kind, lambda _url: self._on_poster_ready())

    def is_resolved(self, it: dict, kind: str) -> bool:
        """True when we've either already got TMDB metadata for this row or
        TMDB isn't going to answer for it (live TV, no TMDB provider
        configured, empty title). The list delegate uses this to decide when
        it's safe to load the provider fallback cover: while TMDB is
        mid-fetch, painting the fallback would just be a wasted network
        round-trip that gets replaced 150 ms later when TMDB resolves."""
        if not self.resolver or kind not in ("vod", "series"):
            return True
        title = it.get("name") or it.get("title") or ""
        if not title:
            return True
        return self.resolver.is_resolved(title, kind)

    @staticmethod
    def _provider_cover(it: dict) -> str | None:
        raw = it.get("stream_icon") or it.get("cover")
        return raw or None

    @staticmethod
    def _cover_kind(it: dict, kind: str) -> str:
        """The kind to use for artwork lookup. Watch Later / Watched /
        Favourites / History rows are snapshots that carry the real
        content kind in "_kind"; map that to vod/series so their posters
        resolve from TMDB just like the Movies/Series lists, instead of
        being treated as a container kind that never gets a poster."""
        if kind in ("watchlist", "watched", "fav", "history"):
            hk = it.get("_kind")
            return {"movie": "vod", "vod": "vod", "series": "series",
                    "episode": "series"}.get(hk, kind)
        return kind

    def cover_url(self, it: dict, kind: str) -> str | None:
        """The URL the list delegate should paint for this row, chosen
        from an ordered candidate list (first that isn't blacklisted):

          1. TMDB poster from the title search (matches the detail
             panel, so the two columns agree once it resolves)
          2. TMDB poster extracted from the provider's own image URL
             (many panels proxy TMDB art under a broken host - going
             to image.tmdb.org gets the real file with no title-search
             dependency and no wait)
          3. the raw provider URL

        poster_for() is always called so the background TMDB lookup
        that feeds the watched-badge + detail panel still fires, even
        when the user prefers the provider's own artwork for the list -
        in that case we just drop the title-search poster as the *cover*
        candidate and let the provider image (or its embedded-TMDB
        rewrite) win."""
        eff = self._cover_kind(it, kind)
        # A Trakt-only row already carries a real TMDB poster URL as its
        # stream_icon, so skip the title search (it would be a wasted
        # round-trip that could even mismatch) and use that directly.
        title_tmdb = None if it.get("_trakt_only") else self.poster_for(it, eff)
        if not self.prefer_tmdb:
            title_tmdb = None
        return choose_cover_url(
            title_tmdb, self._provider_cover(it),
            eff, self._logos.is_dead)

    def should_fetch(self, url: Any, it: dict, kind: str) -> bool:
        """Whether the delegate should queue a network fetch for *url*
        now. TMDB URLs (title-search or embedded) fetch immediately;
        the raw provider URL waits until the TMDB lookup has answered
        so a pending row doesn't burn a request on art that's about to
        be replaced (and hammer flaky panel hosts into rate-limiting)."""
        if (not url or url in self._logos.waiting
                or self._logos.is_dead(url)):
            return False
        if is_tmdb_image_url(url):
            return True
        # When the user prefers the provider's own artwork, there's no
        # pending title-search poster that could replace this URL, so
        # fetch it straight away instead of waiting on TMDB.
        if not self.prefer_tmdb:
            return True
        return self.is_resolved(it, self._cover_kind(it, kind))
