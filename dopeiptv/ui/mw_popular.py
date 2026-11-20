"""Popular right now, for the places that show it at their top (Movies,
Series): TMDB's chart of the week (services/popular.py) matched against
the active source's own catalogue - only what the source has, in the
chart's order, never what its lists leave out (hidden or locked
categories, a kids profile's limits).

``popular_titles(kind, done)`` hands the rows to *done* on the window's
thread: at once when they are known, else once the catalogue is read on a
worker (the source's lists - never a stream). The chart is fetched in the
background once a day (through the TMDB key the covers use); when a new
one comes the watchers (``popular_watch``) are told, and ask again.
Nothing while ``popular_on(kind)`` is off (Settings > Posters & info, or
Just the list on Settings > Appearance).
"""
from __future__ import annotations

import os
import time
from typing import Callable

from PyQt6.QtCore import QStandardPaths

from ..core import places
from ..core.log import log
from ..core.workers import run_async
from ..i18n import current_language
from ..services import popular


def _is_movie(it: dict) -> bool:
    """A real film row: a stream id and no live or radio type (some
    providers mix channels into the films' list)."""
    if it.get("stream_id") is None:
        return False
    st = str(it.get("stream_type") or "").lower()
    return st not in ("live", "radio", "created_live")


class _PopularMixin:
    """MainWindow mixin: popular right now, matched to the source."""

    POPULAR_MAX = 18
    #: The catalogue matched is kept this long (per source and chart).
    POPULAR_KEEP_S = 30 * 60

    def popular_on(self, kind: str) -> bool:
        """Whether popular right now goes at the top of Movies ("vod") or
        Series ("series"): switched on (Settings > Posters & info) and Just
        the list off (core/places.py) - nothing is asked for meanwhile."""
        return places.popular_on(self.settings, kind) and not places.just_list(self.settings)

    def popular_watch(self, fn: Callable[[], None]) -> None:
        """*fn* is called when a new chart came (or the source changed):
        the rows asked for before may be others now."""
        watchers = self.__dict__.setdefault("_popular_watchers", [])
        if fn not in watchers:
            watchers.append(fn)

    def popular_titles(self, kind: str, done: Callable[[list], None],
                       limit: int | None = None) -> None:
        """The source's titles on this week's chart, for *kind* "vod" or
        "series", to *done* (on the window's thread; [] when there is no
        chart yet, no TMDB key, or it is switched off)."""
        limit = limit or self.POPULAR_MAX
        if kind not in ("vod", "series") or not self.popular_on(kind):
            done([])
            return
        chart = self._popular_chart_now()
        if not chart:
            done([])
            return
        client = getattr(self, "client", None)
        stamp = (id(client), chart.get("t"))
        kept = self.__dict__.setdefault("_popular_kept", {})
        hit = kept.get(kind)
        if hit is not None and hit[0] == stamp and time.time() - hit[1] < self.POPULAR_KEEP_S:
            done(self._popular_pick(hit[2], kind, limit))
            return
        if client is None:
            done([])
            return
        resolver = getattr(getattr(self, "cover", None), "resolver", None)

        def known(name: str):
            return resolver.tmdb_id_for(name, kind) if resolver is not None else None

        rows_of = client.vod_streams if kind == "vod" else client.series_list
        entries = chart.get(kind) or []

        def work():
            # Tens of thousands of names: matched off the window's thread.
            return popular.match(entries, list(rows_of(None) or []),
                                 known if resolver is not None else None)

        def got(groups):
            kept[kind] = (stamp, time.time(), groups)
            if getattr(self, "client", None) is not client:
                return                      # another source meanwhile
            done(self._popular_pick(groups, kind, limit))

        def failed(msg):
            log.info("popular: the catalogue could not be read: %s", msg)
            done([])

        run_async(self.pool, work, got, failed)

    def _popular_pick(self, groups: list, kind: str, limit: int) -> list[dict]:
        """For each title on the chart, the first copy the person may see:
        not hidden, not locked away, not for adults on a kids profile, and
        for films a real film."""
        flat = [row for rows in groups for row in rows]
        seen = {id(r) for r in self._visible_catalog(flat, kind)}
        kids = getattr(self, "_kids_only", None)
        if callable(kids):
            seen &= {id(r) for r in kids(flat, kind)}

        def ok(row: dict) -> bool:
            if id(row) not in seen:
                return False
            if kind == "vod":
                return _is_movie(row)
            return row.get("series_id") is not None

        return popular.pick(groups, ok, limit)

    def _popular_chart_now(self) -> dict | None:
        """This week's chart, from memory or disk - fetched again in the
        background once it is a day old or in another language."""
        if getattr(getattr(self, "cover", None), "off", False):
            return None                     # TMDB turned off: no chart, not even the kept one
        if getattr(self, "_popular_chart", None) is None:
            self._popular_chart = popular.load(self._popular_chart_path()) or {}
        chart = self._popular_chart
        now, lang = time.time(), current_language()
        resolver = getattr(getattr(self, "cover", None), "resolver", None)
        if resolver is None and not getattr(self, "_popular_nokey_said", False):
            self._popular_nokey_said = True
            log.info("popular: no TMDB key, so no chart (a source checkout has no "
                     "built-in key: set DOPEIPTV_TMDB_KEY, or a key of your own in "
                     "Settings)")
        if (resolver is not None and not popular.is_fresh(chart, lang, now)
                and not getattr(self, "_popular_fetching", False)
                and now - getattr(self, "_popular_failed", 0.0) > popular.RETRY_AFTER_S):
            self._popular_fetch(resolver.client, lang)
        return chart if popular.usable(chart, now) else None

    def _popular_fetch(self, client, lang: str) -> None:
        self._popular_fetching = True
        path = self._popular_chart_path()

        def work():
            chart = popular.fetch(client, lang)
            if path:
                try:
                    popular.save(path, chart)
                except OSError as e:
                    log.info("popular: could not keep the chart: %s", e)
            return chart

        def done(chart):
            self._popular_fetching = False
            self._popular_chart = chart
            log.info("popular: TMDB chart of %d movies and %d series",
                     len(chart["vod"]), len(chart["series"]))
            self._popular_changed()

        def failed(msg):
            self._popular_fetching = False
            self._popular_failed = time.time()
            log.info("popular: no TMDB chart: %s", msg)

        run_async(self.pool, work, done, failed)

    def _popular_changed(self) -> None:
        """A new chart, or another source: what was matched goes, and the
        places showing it ask again."""
        self.__dict__["_popular_kept"] = {}
        for fn in list(self.__dict__.get("_popular_watchers", [])):
            try:
                fn()
            except Exception:
                log.exception("popular: a watcher")

    @staticmethod
    def _popular_chart_path() -> str:
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        return os.path.join(base, "tmdb_trending.json") if base else ""
