"""Extracted from main_window.py (mixin); MainWindow inherits this.

Verbatim move - self.* access and behaviour unchanged.
"""

from __future__ import annotations

import secrets
import threading
import time
from ..i18n import tr
from ..providers.metadata import PosterResolver
from ..providers.oauth_loopback import capture_oauth_redirect
from ..providers.trakt import OAUTH_PORT, REDIRECT_URI
from .theme import P
from ..core.workers import run_async
from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QTabWidget, QVBoxLayout)
from datetime import datetime


class _TraktMixin:
    """MainWindow mixin: Trakt sign-in, watched-history sync and the watchlist/history view."""
    def _trakt_start_for_item(self, kind: str, item) -> None:
        if not item or not self.trakt.is_connected():
            return
        if kind == "movie":
            title = item.get("name") or item.get("title") or ""
            if not title:
                return

            def job(title=title):
                movie = self.trakt.find_movie(title)
                if not movie:
                    return None
                payload = {"movie": movie}
                self.trakt.scrobble_start(payload, 0.0)
                return payload

        elif kind == "episode":
            show_title = ((self.series_ctx or {}).get("name")
                          or (self.series_ctx or {}).get("title") or "")
            try:
                season = int(item.get("season"))
                episode = int(item.get("episode_num"))
            except (TypeError, ValueError):
                return
            if not show_title:
                return

            def job(show_title=show_title, season=season, episode=episode):
                ep = self.trakt.find_episode(show_title, season, episode)
                if not ep:
                    return None
                payload = {"episode": ep}
                self.trakt.scrobble_start(payload, 0.0)
                return payload
        else:
            return

        def done(payload):
            if payload:
                self._trakt_active = {"payload": payload}

        run_async(self.pool, job, done, lambda _e: None)

    def _trakt_stop_current(self) -> None:
        if not self._trakt_active:
            return
        active = self._trakt_active
        self._trakt_active = None
        progress = self.player.progress_percent() if self.player else 0.0

        def job(active=active, progress=progress):
            self.trakt.scrobble_stop(active["payload"], progress)

        run_async(self.pool, job, lambda _r: None, lambda _e: None)

    # -- trakt watched-history sync --------------------------------------------

    # Skip the auto-sync if we synced this recently (an hour) - avoids
    # hammering Trakt on every restart while still catching the "watched a
    # movie on the couch, opened dopeIPTV the same evening" case.
    _WATCHED_SYNC_TTL: int = 3600

    def _maybe_sync_watched(self, force: bool = False) -> None:
        if not self.trakt.is_connected() or self._watched_sync_running:
            return
        if not force:
            age = int(datetime.now().timestamp()) - self.watched.last_sync_at
            if age < self._WATCHED_SYNC_TTL:
                return
        self._watched_sync_running = True

        # Runs on a daemon thread rather than the QThreadPool: the two
        # Trakt endpoints are 30-45 s worst case, and if the user closes
        # the app while the request is in flight a pool worker touching
        # emit signals during Python teardown segfaults the process on
        # exit. Daemon threads are killed instantly on process end so
        # no callback ever fires into a torn-down interpreter.
        def worker() -> None:
            movie_titles: dict[int, str] = {}
            show_titles: dict[int, str] = {}
            try:
                movies = self.trakt.watched_movies(movie_titles)
                shows = self.trakt.watched_shows(show_titles)
                wl_movies = self.trakt.watchlist_movies()
                wl_shows = self.trakt.watchlist_shows()
            except Exception:
                # Marshal back to the main thread just to flip the
                # running flag; QTimer.singleShot from a non-Qt thread
                # is safe as long as the target QObject lives there.
                QTimer.singleShot(0, self._on_watched_sync_failed)
                return
            self.watched.replace(movies, shows, movie_titles, show_titles)
            self.watchlist.replace(wl_movies, wl_shows)
            # Cross-device: anything the user marked 'seen (local)' that
            # has since resolved to a TMDB id but isn't on Trakt yet gets
            # pushed up now, then promoted into the Trakt layer so it's
            # never POSTed twice (which would add duplicate watches).
            self._push_local_watched_to_trakt()
            QTimer.singleShot(0, self._on_watched_sync_done)

        threading.Thread(target=worker, daemon=True).start()

    def _push_local_watched_to_trakt(self) -> None:
        """Runs on the sync worker thread. Best-effort POST of local-only
        watched marks to Trakt; each accepted item is promoted to the
        Trakt layer so the next sync keeps it and we never double-post."""
        movies, episodes = self.watched.pending_trakt_pushes()
        for tid in movies:
            try:
                self.trakt.add_movie_history(tid)
            except Exception:
                continue
            self.watched.mark_movie_synced(tid)
        for show_id, season, episode in episodes:
            try:
                self.trakt.add_episode_history(show_id, season, episode)
            except Exception:
                continue
            self.watched.mark_episode_synced(show_id, season, episode)
        for show_id in self.watched.pending_show_pushes():
            try:
                self.trakt.add_show_history(show_id)
            except Exception:
                continue
            self.watched.mark_show_synced(show_id)
        wl_movies, wl_shows = self.watchlist.pending_trakt_pushes()
        for tid in wl_movies:
            try:
                self.trakt.add_movie_watchlist(tid)
            except Exception:
                continue
            self.watchlist.mark_movie_synced(tid)
        for tid in wl_shows:
            try:
                self.trakt.add_show_watchlist(tid)
            except Exception:
                continue
            self.watchlist.mark_show_synced(tid)
        self._push_local_favorites_to_trakt()

    def _push_local_favorites_to_trakt(self) -> None:
        """Runs on the sync worker thread. Upload any movie/series
        favourite that carries a resolved tmdb id but isn't on the Trakt
        'dopeIPTV Favorites' list yet, so favourites are remembered
        across devices. Adds are idempotent, so re-runs are harmless."""
        try:
            on_trakt = {"vod": set(self.trakt.favorite_movies()),
                        "series": set(self.trakt.favorite_shows())}
        except Exception:
            return
        for store, kind in ((self.movie_favs, "vod"),
                            (self.series_favs, "series")):
            for it in store.items():
                tid = it.get("_tmdb_id")
                if isinstance(tid, int) and tid not in on_trakt[kind]:
                    try:
                        self.trakt.add_favorite(tid, kind)
                    except Exception:
                        pass

    def _on_watched_sync_done(self) -> None:
        self._watched_sync_running = False
        # Nudge the visible list so the newly-known 'seen' markers
        # paint without needing a category switch.
        self.list_model.refresh_all()
        # The Watched list is built from the Trakt sets, so a fresh sync
        # means it needs rebuilding, not just repainting.
        if self.mode == "watched":
            self._load_items(self._watched_subcat)

    def _on_watched_sync_failed(self) -> None:
        self._watched_sync_running = False

    # -- Watched (Sedda) list ------------------------------------------------

    def _reload_watched(self) -> None:
        if self.mode == "watched":
            self._load_items(self._watched_subcat)

    def _update_sync_btn(self) -> None:
        """Show the sidebar 'Sync now' button only in the Trakt-backed
        lists, and only when a Trakt account is connected."""
        cur = self.cat_list.currentItem()
        data = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        show = self.trakt.is_connected() and (
            self.mode == "watched"
            or (self.mode == "fav" and isinstance(data, tuple)
                and data[0] == "trakt"))
        # Never on the collapsed icon rail - a red text button has no place
        # there, and it belongs to the expanded sidebar only.
        show = show and not getattr(self, "_sidebar_collapsed", False)
        self._sync_now_btn.setVisible(bool(show))

    def _sidebar_sync_now(self) -> None:
        # Force a Trakt pull (watched history + watchlist) and push any
        # local marks/favourites up; the Watched list rebuilds itself on
        # completion. In the Favorites -> Trakt view, also refetch that
        # list right away.
        self._maybe_sync_watched(force=True)
        if self.mode == "fav" and self._fav_section == "trakt":
            self._load_trakt_favorites()
        self._set_status(tr("trakt_syncing"))

    def _trakt_watched_item(self, tmdb_id: int, kind: str,
                            meta: dict | None) -> dict:
        """Build a list row for a Trakt-watched title. meta is the
        resolved {name, poster_url} or None while the tmdb-id lookup is
        still in flight (shows a placeholder that fills in on repaint)."""
        # Prefer the resolved TMDB name; fall back to the title Trakt gave us
        # (so rows are named even with no TMDB key), then a placeholder.
        name = ((meta or {}).get("name")
                or self.watched.trakt_title(tmdb_id, kind) or "…")
        poster = (meta or {}).get("poster_url")
        item = {"name": name, "_kind": kind, "_tmdb_id": tmdb_id,
                "_trakt_only": True}
        if poster:
            item["stream_icon"] = poster
        return item

    def _trakt_watched_items(self) -> list[dict]:
        """The whole Trakt watched history as list rows - movies plus one
        row per series. tmdb-ids are resolved to name + poster lazily via
        the resolver's by-id cache; unresolved rows trigger a background
        fetch and a debounced rebuild so they fill in as answers arrive."""
        def on_resolved(_meta):
            self._watched_refresh_timer.start(250)

        items: list[dict] = []
        for tid in sorted(self.watched.trakt_movies):
            meta = (self.tmdb.resolve_by_id(tid, "vod", on_resolved)
                    if self.tmdb else None)
            items.append(self._trakt_watched_item(tid, "vod", meta))
        # Shows Trakt has returned episodes for, PLUS whole-show marks we
        # pushed this session - the latter may not be reflected in the
        # /sync/watched pull yet (Trakt indexes with a short delay), so
        # include them so a just-synced series shows up immediately.
        show_ids = set(self.watched.trakt_episodes) | set(
            self.watched.synced_shows)
        for show_tid in sorted(show_ids):
            meta = (self.tmdb.resolve_by_id(show_tid, "series", on_resolved)
                    if self.tmdb else None)
            items.append(self._trakt_watched_item(show_tid, "series", meta))
        return items

    def _load_trakt_favorites(self) -> None:
        """Fetch the 'dopeIPTV Favorites' list ids from Trakt off the GUI
        thread, then build the rows (resolving names/posters by id)."""
        if not self.trakt.is_connected():
            return
        gen = self._load_gen

        def job():
            return (self.trakt.favorite_movies(), self.trakt.favorite_shows())

        def done(res):
            if (gen != self._load_gen or self.mode != "fav"
                    or self._fav_section != "trakt"):
                return
            self._fav_trakt_ids = (res[0] or [], res[1] or [])
            self._rebuild_fav_trakt()

        run_async(self.pool, job, done, lambda _e: None)

    def _rebuild_fav_trakt(self) -> None:
        if self.mode != "fav" or self._fav_section != "trakt":
            return
        movies, shows = self._fav_trakt_ids

        def on_resolved(_meta):
            self._fav_refresh_timer.start(250)

        movie_items = []
        for tid in movies:
            meta = (self.tmdb.resolve_by_id(tid, "vod", on_resolved)
                    if self.tmdb else None)
            movie_items.append(self._trakt_watched_item(tid, "vod", meta))
        series_items = []
        for tid in shows:
            meta = (self.tmdb.resolve_by_id(tid, "series", on_resolved)
                    if self.tmdb else None)
            series_items.append(self._trakt_watched_item(tid, "series", meta))
        # Split into Movies and Series under headers, like the other views.
        self._show_grouped(
            [("fav_movies", "vod", "vod", self._search_filter(movie_items)),
             ("fav_series", "series", "series",
              self._search_filter(series_items))],
            "fav")

    @staticmethod
    def _merge_watched(local: list[dict],
                       trakt: list[dict]) -> list[dict]:
        """Local snapshots first (they carry provider ids so they're
        playable), then Trakt-only rows whose tmdb-id isn't already
        covered by a local entry."""
        seen = {(x.get("_kind"), x.get("_tmdb_id"))
                for x in local if x.get("_tmdb_id") is not None}
        merged = list(local)
        for t in trakt:
            if (t.get("_kind"), t.get("_tmdb_id")) in seen:
                continue
            merged.append(t)
        return merged

    def is_movie_watched(self, item: dict) -> bool:
        if not item:
            return False
        if self.watched.is_movie_watched(self._tmdb_id_for_item(item, "vod")):
            return True
        sid = item.get("stream_id")
        try:
            return self.watched.is_movie_watched_by_stream(int(sid))
        except (TypeError, ValueError):
            return False

    def show_watched_count(self, item: dict) -> int:
        if not item:
            return 0
        count = self.watched.show_watched_count(
            self._tmdb_id_for_item(item, "series"))
        if count > 0:
            return count
        # Fall back to the stream-id local mark so a series marked
        # watched before TMDB resolved still shows a badge (count 1).
        sid = item.get("series_id") or item.get("stream_id")
        try:
            if self.watched.is_series_watched_by_stream(int(sid)):
                return 1
        except (TypeError, ValueError):
            pass
        return 0

    def is_episode_watched(self, item: dict) -> bool:
        if not item or not self.series_ctx:
            return False
        if self.tmdb:
            show_title = (self.series_ctx.get("name")
                          or self.series_ctx.get("title") or "")
            show_id = self.tmdb.tmdb_id_for(show_title, "series")
            try:
                season = int(item.get("season"))
                episode = int(item.get("episode_num"))
                if self.watched.is_episode_watched(show_id, season, episode):
                    return True
            except (TypeError, ValueError):
                pass
        eid = item.get("id")
        try:
            return self.watched.is_episode_watched_by_stream(int(eid))
        except (TypeError, ValueError):
            return False

    def is_item_watched(self, item: dict, kind: str) -> bool:
        """Unified predicate the delegate calls once per paint - answers
        whether the given item should show the 'already seen' badge."""
        if kind == "vod":
            return self.is_movie_watched(item)
        if kind == "series":
            return self.show_watched_count(item) > 0
        if kind == "episode":
            return self.is_episode_watched(item)
        if kind == "history":
            hk = item.get("_kind")
            if hk == "movie":
                return self.is_movie_watched(item)
            if hk == "series":
                return self.show_watched_count(item) > 0
        return False

    def watched_source(self, item: dict, kind: str) -> str | None:
        """'trakt' if this row is watched according to Trakt, 'local' if
        it's only a local in-app mark, else None. Lets the delegate
        colour the badge differently so a Trakt-synced 'seen' reads
        distinctly from a local-only one."""
        if kind == "history":
            hk = item.get("_kind")
            kind = {"movie": "vod", "series": "series"}.get(hk, hk)
        if kind == "vod":
            if not self.is_movie_watched(item):
                return None
            tid = self._tmdb_id_for_item(item, "vod")
            if isinstance(tid, int) and tid in self.watched.trakt_movies:
                return "trakt"
            return "local"
        if kind == "series":
            tid = self._tmdb_id_for_item(item, "series")
            if isinstance(tid, int) and (
                    self.watched.trakt_episodes.get(tid)
                    or tid in self.watched.synced_shows):
                return "trakt"
            if self.show_watched_count(item) > 0:
                return "local"
            return None
        if kind == "episode":
            if not self.is_episode_watched(item):
                return None
            tid = self._show_tmdb_id_for_episode()
            try:
                pair = (int(item.get("season")), int(item.get("episode_num")))
            except (TypeError, ValueError):
                pair = None
            if (isinstance(tid, int) and pair is not None
                    and pair in (self.watched.trakt_episodes.get(tid) or set())):
                return "trakt"
            return "local"
        return None

    def is_favorite_item(self, item: dict, kind: str) -> bool:
        """Whether the row is a favourite (its gold star) - routed to the
        right store: channels, movies (stream_id) or series (series_id)."""
        if not item:
            return False
        if kind == "history":
            hk = item.get("_kind")
            kind = {"movie": "vod", "series": "series"}.get(hk, hk)
        if kind in ("live", "fav"):
            return self.favs.is_favorite(item.get("stream_id"))
        if kind == "vod":
            return self.movie_favs.is_favorite(item.get("stream_id"))
        if kind == "series":
            return self.series_favs.is_favorite(item.get("series_id"))
        return False

    def is_item_on_watchlist(self, item: dict, kind: str) -> bool:
        """Unified predicate the delegate calls once per paint - answers
        whether the given item is on the Watch Later list (its clock
        marker). Only movie/series rows can be on the list."""
        if kind == "vod":
            return self.is_on_watchlist(item, "vod")
        if kind == "series":
            return self.is_on_watchlist(item, "series")
        if kind == "history":
            hk = item.get("_kind")
            if hk == "movie":
                return self.is_on_watchlist(item, "vod")
            if hk == "series":
                return self.is_on_watchlist(item, "series")
        return False

    def _tmdb_id_for_item(self, item: dict, kind: str) -> int | None:
        if not item:
            return None
        # Snapshots (Watch Later / Watched / Trakt rows) already carry a
        # resolved tmdb id - trust it directly so mark/unmark and the
        # badges work even without a title-search cache hit.
        tid = item.get("_tmdb_id")
        if isinstance(tid, int):
            return tid
        if not self.tmdb:
            return None
        title = item.get("name") or item.get("title") or ""
        return self.tmdb.tmdb_id_for(title, kind)

    def _show_tmdb_id_for_episode(self) -> int | None:
        if not self.tmdb or not self.series_ctx:
            return None
        show_title = (self.series_ctx.get("name")
                      or self.series_ctx.get("title") or "")
        return self.tmdb.tmdb_id_for(show_title, "series")

    # -- mark-as-watched (local toggle + optional Trakt push) -----------------

    @staticmethod
    def _trakt_push(fn) -> None:
        """Fire a Trakt mutation on a daemon thread (killed instantly on
        quit, so a mid-flight request never crashes teardown)."""
        def job():
            try:
                fn()
            except Exception:
                pass
        threading.Thread(target=job, daemon=True).start()

    def _resolve_tmdb_id_async(self, item: dict, kind: str,
                               on_id) -> None:
        """Look a provider title up on TMDB (search -> id) off the GUI
        thread and hand the resolved id (or None) back on the main
        thread. Used by the '+Trakt' mark path when the poster hasn't
        resolved yet so choosing 'seen (Trakt)' still reaches Trakt."""
        title = item.get("name") or item.get("title") or ""
        if not title or not self.tmdb:
            on_id(None)
            return

        def job():
            cleaned, year = PosterResolver.clean_title(title)
            details = self.tmdb.client.fetch_details(
                cleaned or title, kind, year)
            return details.get("tmdb_id") if details else None

        run_async(self.pool, job, on_id, lambda _e: on_id(None))

    def _mark_movie_watched(self, item: dict,
                            push_to_trakt: bool) -> None:
        tid = self._tmdb_id_for_item(item, "vod")
        if tid is not None:
            self.watched.mark_movie_local(tid)
        else:
            # Local mark works even before TMDB resolves - key on the
            # provider stream_id so the badge is immediate.
            sid = item.get("stream_id")
            try:
                self.watched.mark_movie_local_by_stream(int(sid))
            except (TypeError, ValueError):
                return
        self.watched.add_local_item(item, "vod", tid)
        if push_to_trakt and self.trakt.is_connected():
            if tid is not None:
                self._trakt_push(lambda: self.trakt.add_movie_history(tid))
            elif self.tmdb is not None:
                # Not resolved yet - look the id up on demand, then
                # upgrade the local mark to tmdb-keyed and push.
                self._resolve_tmdb_id_async(
                    item, "vod",
                    lambda tid: self._on_movie_id_for_trakt(item, tid))
            else:
                self._error(tr("mark_needs_tmdb"))
        self.list_model.refresh_all()

    def _on_movie_id_for_trakt(self, item: dict, tid: int | None) -> None:
        if not isinstance(tid, int):
            self._error(tr("mark_needs_tmdb"))
            return
        self.watched.mark_movie_local(tid)
        self.watched.add_local_item(item, "vod", tid)
        self._trakt_push(lambda: self.trakt.add_movie_history(tid))
        self.list_model.refresh_all()

    def _unmark_movie_watched(self, item: dict,
                              push_to_trakt: bool) -> None:
        tid = self._tmdb_id_for_item(item, "vod")
        if tid is not None:
            self.watched.unmark_movie(tid)
        sid = item.get("stream_id")
        try:
            self.watched.unmark_movie_by_stream(int(sid))
        except (TypeError, ValueError):
            pass
        self.watched.remove_local_item("vod", tid, item.get("stream_id"))
        if push_to_trakt and tid is not None and self.trakt.is_connected():
            def job(tid=tid):
                try:
                    self.trakt.remove_movie_history(tid)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        self.list_model.refresh_all()

    def _mark_episode_watched(self, item: dict,
                              push_to_trakt: bool) -> None:
        sid = self._show_tmdb_id_for_episode()
        try:
            season = int(item.get("season"))
            episode = int(item.get("episode_num"))
        except (TypeError, ValueError):
            season = episode = None
        if sid is not None and season is not None:
            self.watched.mark_episode_local(sid, season, episode)
        else:
            eid = item.get("id")
            try:
                self.watched.mark_episode_local_by_stream(int(eid))
            except (TypeError, ValueError):
                return
        # Watching an episode means the show belongs in the local
        # watched list - snapshot the series (one row per show).
        if self.series_ctx:
            self.watched.add_local_item(self.series_ctx, "series", sid)
        if (push_to_trakt and season is not None
                and self.trakt.is_connected()):
            if sid is not None:
                self._trakt_push(
                    lambda: self.trakt.add_episode_history(sid, season, episode))
            elif self.tmdb is not None and self.series_ctx is not None:
                self._resolve_tmdb_id_async(
                    self.series_ctx, "series",
                    lambda tid, s=season, e=episode: self._on_show_id_for_trakt(
                        self.series_ctx, tid, s, e))
            else:
                self._error(tr("mark_needs_tmdb"))
        self.list_model.refresh_all()

    def _on_show_id_for_trakt(self, series_item: dict, tid: int | None,
                              season: int, episode: int) -> None:
        if not isinstance(tid, int):
            self._error(tr("mark_needs_tmdb"))
            return
        self.watched.mark_episode_local(tid, season, episode)
        self.watched.add_local_item(series_item, "series", tid)
        self._trakt_push(
            lambda: self.trakt.add_episode_history(tid, season, episode))
        self.list_model.refresh_all()

    def _unmark_episode_watched(self, item: dict,
                                push_to_trakt: bool) -> None:
        sid = self._show_tmdb_id_for_episode()
        try:
            season = int(item.get("season"))
            episode = int(item.get("episode_num"))
        except (TypeError, ValueError):
            season = episode = None
        if sid is not None and season is not None:
            self.watched.unmark_episode(sid, season, episode)
        eid = item.get("id")
        try:
            self.watched.unmark_episode_by_stream(int(eid))
        except (TypeError, ValueError):
            pass
        # Drop the series from the local watched list only once no
        # episodes remain watched and it isn't marked as a whole-show.
        if self.series_ctx:
            series_sid = (self.series_ctx.get("series_id")
                          or self.series_ctx.get("stream_id"))
            still_watched = (
                (isinstance(sid, int) and self.watched.show_watched_count(sid))
                or self.watched.is_series_watched_by_stream(
                    self._as_int(series_sid)))
            if not still_watched:
                self.watched.remove_local_item("series", sid, series_sid)
        if (push_to_trakt and sid is not None and season is not None
                and self.trakt.is_connected()):
            def job(sid=sid, s=season, e=episode):
                try:
                    self.trakt.remove_episode_history(sid, s, e)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        self.list_model.refresh_all()

    def _mark_series_watched(self, item: dict,
                             push_to_trakt: bool) -> None:
        """'Seen the whole show' toggle. Locally it's a stream-keyed
        flag; the +Trakt variant marks the entire show watched on Trakt
        (a show payload adds every aired episode)."""
        sid = item.get("series_id") or item.get("stream_id")
        try:
            self.watched.mark_series_local_by_stream(int(sid))
        except (TypeError, ValueError):
            return
        tid = self._tmdb_id_for_item(item, "series")
        self.watched.add_local_item(item, "series", tid)
        if isinstance(tid, int):
            # Record the whole-show mark (tmdb-keyed) so the periodic sync
            # can push it to Trakt even if the user chose the local-only
            # variant; the +Trakt variant also pushes right away.
            self.watched.mark_show_local(tid)
            if push_to_trakt and self.trakt.is_connected():
                self._trakt_push(lambda: self.trakt.add_show_history(tid))
                self.watched.mark_show_synced(tid)
        elif self.tmdb is not None:
            # No cached id yet - resolve it in the background so the show
            # can still reach Trakt (now if +Trakt, otherwise on the next
            # sync). Without a resolved id there's nothing Trakt can key
            # on, hence the mark_needs_tmdb notice only for the +Trakt
            # variant when we have no resolver at all.
            self._resolve_tmdb_id_async(
                item, "series",
                lambda rid: self._on_series_id_resolved(item, rid,
                                                        push_to_trakt))
        elif push_to_trakt:
            self._error(tr("mark_needs_tmdb"))
        self.list_model.refresh_all()

    def _on_series_id_resolved(self, item: dict, tid: int | None,
                               push_to_trakt: bool) -> None:
        if not isinstance(tid, int):
            if push_to_trakt:
                self._error(tr("mark_needs_tmdb"))
            return
        self.watched.mark_show_local(tid)
        self.watched.add_local_item(item, "series", tid)
        if push_to_trakt and self.trakt.is_connected():
            self.watched.mark_show_synced(tid)
            self._trakt_push(lambda: self.trakt.add_show_history(tid))
        self.list_model.refresh_all()

    def _unmark_series_watched(self, item: dict,
                               push_to_trakt: bool) -> None:
        sid = item.get("series_id") or item.get("stream_id")
        try:
            self.watched.unmark_series_by_stream(int(sid))
        except (TypeError, ValueError):
            pass
        # Also clear any TMDB-keyed per-episode marks + whole-show mark
        # for this show so the badge disappears immediately.
        tid = self._tmdb_id_for_item(item, "series")
        if isinstance(tid, int):
            for ep_set in (self.watched.trakt_episodes,
                           self.watched.local_episodes):
                ep_set.pop(tid, None)
            self.watched.unmark_show(tid)
            self.watched._save()
        self.watched.remove_local_item(
            "series", tid, item.get("series_id") or item.get("stream_id"))
        if (push_to_trakt and isinstance(tid, int)
                and self.trakt.is_connected()):
            self._trakt_push(lambda: self.trakt.remove_show_history(tid))
        self.list_model.refresh_all()

    # -- Watch Later (local toggle + optional Trakt push) --------------------

    def is_on_watchlist(self, item: dict, kind: str) -> bool:
        tid = self._tmdb_id_for_item(item, kind)
        sid = (item.get("stream_id") if kind == "vod"
               else item.get("series_id"))
        try:
            sid_int = int(sid) if sid is not None else None
        except (TypeError, ValueError):
            sid_int = None
        if kind == "vod":
            return self.watchlist.has_movie(tid, sid_int)
        if kind == "series":
            return self.watchlist.has_show(tid, sid_int)
        return False

    def _add_watchlist(self, item: dict, kind: str,
                       push_to_trakt: bool) -> None:
        tid = self._tmdb_id_for_item(item, kind)
        if kind == "vod":
            self.watchlist.add_movie_local(item, tid)
            trakt_fn = self.trakt.add_movie_watchlist
        else:
            self.watchlist.add_show_local(item, tid)
            trakt_fn = self.trakt.add_show_watchlist
        if push_to_trakt:
            if tid is None:
                self._error(tr("mark_needs_tmdb"))
            elif self.trakt.is_connected():
                def job(tid=tid, fn=trakt_fn):
                    try:
                        fn(tid)
                    except Exception:
                        pass
                threading.Thread(target=job, daemon=True).start()

    def _remove_watchlist(self, item: dict, kind: str,
                          push_to_trakt: bool) -> None:
        tid = self._tmdb_id_for_item(item, kind)
        sid = (item.get("stream_id") if kind == "vod"
               else item.get("series_id"))
        try:
            sid_int = int(sid) if sid is not None else None
        except (TypeError, ValueError):
            sid_int = None
        if kind == "vod":
            self.watchlist.remove_movie(tid, sid_int)
            trakt_fn = self.trakt.remove_movie_watchlist
        else:
            self.watchlist.remove_show(tid, sid_int)
            trakt_fn = self.trakt.remove_show_watchlist
        if push_to_trakt and tid is not None and self.trakt.is_connected():
            def job(tid=tid, fn=trakt_fn):
                try:
                    fn(tid)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        # If we're currently in the Watch Later view, drop the row.
        if self.mode == "watchlist":
            self._load_items(getattr(self, "_watchlist_subcat", None))

    def _trakt_connect_flow(self, parent) -> None:
        """Entry point for the wizard's 'Connect Trakt' button. If the app
        credentials are already set, go straight to the one-click browser
        sign-in; otherwise collect them first (they're a one-time thing) and
        then continue to the browser."""
        if self.trakt.client_id and self.trakt.client_secret:
            self._trakt_browser_auth_dialog(parent)
            return
        if self._trakt_creds_dialog(parent):
            self._trakt_browser_auth_dialog(parent)

    def _trakt_creds_dialog(self, parent) -> bool:
        """One-time collection of the free Trakt API app's Client ID + Secret.
        Returns True once both are filled and saved."""
        d = QDialog(parent)
        d.setWindowTitle(tr("trakt_connect_title"))
        d.setMinimumWidth(430)
        lay = QVBoxLayout(d)
        intro = QLabel(tr("trakt_wizard_intro", url=REDIRECT_URI))
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size:12px;")
        lay.addWidget(intro)
        create = QPushButton(tr("trakt_create_app"))
        create.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://trakt.tv/oauth/applications/new")))
        crow = QHBoxLayout()
        crow.addWidget(create)
        crow.addStretch(1)
        lay.addLayout(crow)
        form = QFormLayout()
        id_edit = QLineEdit(self.trakt.client_id)
        id_edit.setPlaceholderText(tr("trakt_client_id_ph"))
        sec_edit = QLineEdit(self.trakt.client_secret)
        sec_edit.setPlaceholderText(tr("trakt_client_secret_ph"))
        sec_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr("field_client_id"), id_edit)
        form.addRow(tr("field_client_secret"), sec_edit)
        lay.addLayout(form)
        err = QLabel("")
        err.setStyleSheet(f"color:{P['error']}; font-size:12px;")
        err.setWordWrap(True)
        lay.addWidget(err)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            tr("onb_next"))
        lay.addWidget(buttons)

        def accept() -> None:
            cid = id_edit.text().strip()
            sec = sec_edit.text().strip()
            if not (cid and sec):
                err.setText(tr("msg_trakt_enter_creds"))
                return
            self.settings.setValue("trakt_client_id", cid)
            self.settings.setValue("trakt_client_secret", sec)
            d.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(d.reject)
        return d.exec() == QDialog.DialogCode.Accepted

    def _trakt_browser_auth_dialog(self, parent) -> None:
        """Sign in to Trakt through the browser (OAuth authorization-code with
        a loopback redirect). Opens trakt.tv's approve page; because the
        browser already carries the user's Trakt session they just click
        'Yes' - no code, no password. A one-shot local server on OAUTH_PORT
        catches the redirect and we swap the code for tokens. Falls back to
        the device-code dialog for anyone who'd rather type a code."""
        if not (self.trakt.client_id and self.trakt.client_secret):
            QMessageBox.information(parent, "Trakt", tr("msg_trakt_enter_creds"))
            return

        d = QDialog(parent)
        d.setWindowTitle(tr("trakt_connect_title"))
        d.setMinimumWidth(400)
        lay = QVBoxLayout(d)
        info = QLabel(tr("trakt_browser_opening"))
        info.setWordWrap(True)
        info.setStyleSheet("font-size:13px;")
        lay.addWidget(info)

        row = QHBoxLayout()
        code_btn = QPushButton(tr("trakt_use_code_instead"))
        row.addWidget(code_btn)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        row.addWidget(buttons)
        lay.addLayout(row)

        state = {"cancelled": False, "token": secrets.token_urlsafe(16)}

        def cancel() -> None:
            state["cancelled"] = True
            d.reject()

        buttons.rejected.connect(cancel)

        def use_code() -> None:
            state["cancelled"] = True
            d.reject()
            self._trakt_device_auth_dialog(parent)

        code_btn.clicked.connect(use_code)

        def captured(params) -> None:
            if state["cancelled"]:
                return
            if not params:
                info.setText(tr("trakt_timed_out"))
                return
            if params.get("error"):
                info.setText(tr("trakt_denied"))
                return
            if params.get("state") != state["token"]:
                # Mismatched state: never trust the code (CSRF guard).
                info.setText(tr("trakt_login_failed", msg="state mismatch"))
                return
            info.setText(tr("trakt_finishing"))
            run_async(
                self.pool,
                lambda code=params.get("code", ""): self.trakt.exchange_code(
                    code),
                exchanged, exchange_failed)

        def capture_failed(msg) -> None:
            if not state["cancelled"]:
                info.setText(tr("trakt_port_busy", port=OAUTH_PORT))

        def exchanged(_data) -> None:
            if state["cancelled"]:
                return
            info.setText(tr("trakt_connected_excl"))
            self._maybe_sync_watched(force=True)
            QTimer.singleShot(700, d.accept)

        def exchange_failed(msg) -> None:
            if not state["cancelled"]:
                info.setText(tr("trakt_login_failed", msg=msg))

        # Start the loopback listener first, then open the browser so the
        # server is already waiting when Trakt redirects back.
        run_async(
            self.pool,
            lambda: capture_oauth_redirect(
                OAUTH_PORT, timeout=180.0,
                should_cancel=lambda: state["cancelled"]),
            captured, capture_failed)
        QDesktopServices.openUrl(
            QUrl(self.trakt.authorize_url(state["token"], REDIRECT_URI)))
        d.exec()

    def _trakt_device_auth_dialog(self, parent) -> None:
        d = QDialog(parent)
        d.setWindowTitle(tr("trakt_connect_title"))
        d.setMinimumWidth(380)
        lay = QVBoxLayout(d)
        info = QLabel(tr("trakt_requesting_code"))
        info.setWordWrap(True)
        info.setStyleSheet("font-size:13px;")
        lay.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(d.reject)
        lay.addWidget(buttons)

        state = {"cancelled": False, "device_code": None,
                 "interval": 5, "expires_at": 0.0}

        def poll() -> None:
            if state["cancelled"] or not state["device_code"]:
                return
            if time.time() > state["expires_at"]:
                info.setText(tr("trakt_code_expired"))
                return
            run_async(self.pool,
                      lambda: self.trakt.poll_device_token(
                          state["device_code"]),
                      poll_done, poll_failed)

        def poll_done(data) -> None:
            if state["cancelled"]:
                return
            if data is None:
                QTimer.singleShot(state["interval"] * 1000, poll)
                return
            info.setText(tr("trakt_connected_excl"))
            QTimer.singleShot(700, d.accept)

        def poll_failed(msg) -> None:
            if not state["cancelled"]:
                info.setText(tr("trakt_login_failed", msg=msg))

        def started(data) -> None:
            if state["cancelled"]:
                return
            state["device_code"] = data["device_code"]
            state["interval"] = data.get("interval", 5)
            state["expires_at"] = time.time() + data.get("expires_in", 600)
            info.setText(
                tr("trakt_enter_code",
                   url=data.get('verification_url'),
                   code=data['user_code']))
            QTimer.singleShot(state["interval"] * 1000, poll)

        def start_failed(msg) -> None:
            if not state["cancelled"]:
                info.setText(tr("trakt_could_not_start", msg=msg))

        buttons.rejected.connect(lambda: state.__setitem__("cancelled", True))
        run_async(self.pool, self.trakt.start_device_auth,
                  started, start_failed)
        d.exec()

    def _open_trakt_dialog(self, parent) -> None:
        if not self.trakt.is_connected():
            QMessageBox.information(
                parent, "Trakt", tr("msg_connect_trakt_first"))
            return
        d = QDialog(parent)
        d.setWindowTitle(tr("trakt_watchlist_title"))
        d.setMinimumSize(480, 500)
        lay = QVBoxLayout(d)
        tw = QTabWidget()
        lay.addWidget(tw)
        wl_list = QListWidget()
        hist_list = QListWidget()
        tw.addTab(wl_list, tr("trakt_tab_watchlist"))
        tw.addTab(hist_list, tr("nav_history"))
        status = QLabel(tr("common_loading"))
        status.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        lay.addWidget(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(d.reject)
        buttons.accepted.connect(d.accept)
        lay.addWidget(buttons)

        def fmt_watchlist(e) -> str:
            for kind in ("movie", "show"):
                if e.get(kind):
                    x = e[kind]
                    return f"{x.get('title')} ({x.get('year') or '?'})"
            return "?"

        def fmt_history(e) -> str:
            watched = (e.get("watched_at") or "")[:10]
            if e.get("movie"):
                m = e["movie"]
                return f"{watched}  {m.get('title')} ({m.get('year') or '?'})"
            if e.get("episode") and e.get("show"):
                ep, s = e["episode"], e["show"]
                return (f"{watched}  {s.get('title')} "
                        f"S{ep.get('season')}E{ep.get('number')} - "
                        f"{ep.get('title') or ''}")
            return watched or "?"

        def load_failed(msg) -> None:
            status.setText(tr("trakt_load_failed", msg=msg))

        def load_history(items) -> None:
            hist_list.clear()
            for e in items:
                hist_list.addItem(fmt_history(e))
            status.setText("")

        def load_watchlist(items) -> None:
            wl_list.clear()
            for e in items:
                wl_list.addItem(fmt_watchlist(e))
            run_async(self.pool, lambda: self.trakt.history(50),
                      load_history, load_failed)

        run_async(self.pool, self.trakt.watchlist,
                  load_watchlist, load_failed)
        d.exec()
