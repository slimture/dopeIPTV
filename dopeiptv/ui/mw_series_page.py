"""MainWindow mixin: a series' own page in the middle pane, where the lists
are (ui/series_page.py draws it, core/series_view.py works out what it
shows).

It opens from a series anywhere - its list, Favorites, Watch later,
Home's shelves (Continue watching, New episodes), the command palette,
an actor's other titles, the sidebar's logo for an episode that plays -
and for a local library's series. The episodes come from the code that
always fetched them (the source's series_info, the library's index), off
the window's thread, with a calm word while they load; an answer is kept
a few minutes, so coming back is instant.

The list under the page is never touched: Back (its arrow, Esc, the
mouse's back button) shows it again exactly as it was - category,
scroll, selected row - or the place's own page under it (mw_places). A
category or a nav entry picked meanwhile takes the page away too.

An episode plays in the docked player and the page stays, through the
window's own path (_start_playback) with the series as its context for
the moment - as autoplay, TV mode and watch together do - so resume
points, watched marks, Trakt and Simkl, History and autoplay of the next
episode all go as before; the page follows what plays.

A kids profile never opens a series for adults, a locked category's asks
for the PIN (never a kids profile's), and while viewing privately
nothing is kept - not even the season picked.
"""

from __future__ import annotations

import time
from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QMenu

from ..core import privacy
from ..core import series_view as sv
from ..core.log import log
from ..core.profiles import adult_item
from ..core.workers import LogoLoader, run_async, tmdb_url_from_provider
from ..i18n import tr
from ..media.players import launch_player
from .notices import hub_of
from .series_page import EpisodeState, SeriesPage

#: Where the season a person picked is kept (the profile's own:
#: core/profiles.py OWN_KEYS).
SEASONS_KEY = "series_seasons"
#: A series' episodes are asked for again after this long.
CACHE_SECS = 10 * 60
CACHE_MAX = 24
#: How often the card of what plays is brought up to date.
TICK_MS = 10_000


class _SeriesPageMixin:
    """MainWindow mixin: the series page."""

    _series_page: SeriesPage | None = None
    _sp_gen = 0
    _sp_ctx: dict | None = None
    _sp_local: str | None = None
    _sp_return_search = False
    _sp_seasons: list = []
    _sp_next: sv.NextUp | None = None
    _sp_start_once: tuple | None = None
    _sp_focus: str = ""
    _sp_cache: dict | None = None
    _sp_tmdb: dict | None = None
    _sp_art_loader: LogoLoader | None = None
    _sp_timer: QTimer | None = None
    _sp_head: dict | None = None

    # -- where it is ------------------------------------------------------------------

    def _series_page_showing(self) -> bool:
        page = self._series_page
        stack = getattr(self, "_mid_stack", None)
        return (page is not None and stack is not None
                and stack.currentWidget() is page)

    def _series_page_visible(self) -> bool:
        return self._series_page_showing() and self._series_page.isVisible()

    def _ensure_series_page(self) -> SeriesPage:
        page = self._series_page
        if page is None:
            page = SeriesPage(self.logos)
            # One click plays only with "Auto-play preview on selection" on,
            # as in the lists; else a double click or Enter.
            page.view.click_plays = self._autoplay_preview
            page.back_requested.connect(self._leave_series_page)
            page.play_requested.connect(self._sp_play)
            page.menu_requested.connect(self._sp_episode_menu)
            page.season_shown.connect(self._sp_season_shown)
            # The poster under the video reads the page's episode while the
            # page is up, the list's row once it is gone.
            stack = getattr(self, "_mid_stack", None)
            if stack is not None:
                stack.currentChanged.connect(lambda _i: self._apply_play_icon())
            page.season_picked.connect(self._sp_season_picked)
            page.retry_requested.connect(self._sp_retry)
            h = page.header
            h.primary.connect(self._sp_primary)
            h.favorite.connect(self._sp_toggle_fav)
            h.follow.connect(self._sp_toggle_follow)
            h.later.connect(self._sp_toggle_later)
            h.more.connect(self._sp_more_menu)
            self._series_page = page
            self._mid_stack.addWidget(page)
            t = QTimer(self)
            t.setInterval(TICK_MS)
            t.timeout.connect(self._sp_tick)
            self._sp_timer = t
        return page

    def _sp_art(self) -> LogoLoader:
        """A loader for the header's backdrop, sharp across the pane (the
        list's are made for small pictures); the same disk cache."""
        if self._sp_art_loader is None:
            loader = LogoLoader(self._art_pool, max_size=1280,
                                cache_dir=self.poster_art.disk_dir,
                                max_bytes=24 * 1024 * 1024, disk_budget=0)
            loader.dead = self.logos.dead
            self._sp_art_loader = loader
        return self._sp_art_loader

    # -- opening it -------------------------------------------------------------------

    def _sp_allowed(self, series: dict) -> bool:
        """The parental lock and a kids profile, as the lists have them."""
        kids = getattr(self, "_profile_kids", lambda: False)()
        if kids and (adult_item(series) or adult_item(series.get("_series_ctx") or {})):
            return False
        cat = series.get("category_id")
        if (cat is not None and self.overrides.is_locked("series", cat)
                and not self.parental.session_unlocked):
            if kids or not self._request_unlock():
                return False
        return True

    def _open_series_page(self, series: dict, focus: Any = None,
                          home: bool | None = None, from_search: bool = False) -> None:
        """*series*' page (a series row: its series_id at least), on the
        episode *focus* when one is asked for (what plays, a new one).
        *home*: kept for callers of old (Back goes to the place under the
        page either way). *from_search*: from the Search page (Back goes
        there, its results as they were)."""
        if not isinstance(series, dict) or series.get("series_id") is None:
            return
        if not self._sp_allowed(series):
            log.info("series page: not for this profile or locked")
            return
        same = (self._series_page_showing() and self._sp_local is None
                and str((self._sp_ctx or {}).get("series_id")) == str(series["series_id"]))
        self._sp_local = None
        page = self._sp_show_page(home, from_search)
        if same and self._sp_seasons:
            if focus is not None:
                page.focus_episode(str(focus))
            return
        self._sp_gen += 1
        self._sp_ctx = dict(series)
        self._sp_focus = "" if focus is None else str(focus)
        self._sp_seasons = []
        self._sp_next = None
        self._sp_head = None
        page.reset()
        self._sp_fill_header()
        self._sp_fetch()
        page.view.setFocus()

    def _sp_show_page(self, home: bool | None = None, from_search: bool = False) -> SeriesPage:
        """The page in the middle pane. From the Search page, Search's entry
        is lit (Back goes there); from a place, that place's stays lit (Back
        goes there)."""
        back_search = from_search or (self._series_page_showing() and self._sp_return_search)
        self._close_guide_page()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        page = self._ensure_series_page()
        self._mid_stack.setCurrentWidget(page)
        self._sp_return_search = back_search
        self._check_nav("search" if back_search else self._nav_key_for_mode(self.mode))
        if self._sp_timer is not None:
            self._sp_timer.start()
        return page

    def _sp_fetch(self, fresh: bool = False) -> None:
        page, ctx = self._series_page, self._sp_ctx or {}
        if page is None:
            return
        client, sid, gen = self.client, ctx.get("series_id"), self._sp_gen
        cache = self._sp_cache if self._sp_cache is not None else {}
        self._sp_cache = cache
        hit = cache.get((id(client), str(sid)))
        if hit is not None and not fresh and time.monotonic() - hit[0] < CACHE_SECS:
            self._sp_info_in(gen, client, (True, hit[1]))
            return
        page.set_loading(True, tr("status_loading_episodes"))
        # The activity too, ended by the answer (a newer series replaces it).
        job = hub_of(self).start(tr("status_loading_episodes"), key="series_page",
                                 kind="list", mine=True, timeout_ms=120_000)
        keep = getattr(self, "_kids_keep", lambda _m: None)("series")
        run_async(self.pool, partial(_fetch, client, sid, ctx, keep),
                  partial(self._sp_info_in, gen, client, job=job),
                  partial(self._sp_info_failed, gen, job=job))

    def _sp_retry(self) -> None:
        if self._sp_local is not None:
            self._open_local_series_page(self._sp_local, self._sp_ctx or {})
            return
        self._sp_gen += 1
        self._sp_fetch(fresh=True)

    def _sp_info_failed(self, gen: int, msg, job: int = 0) -> None:
        hub_of(self).finish(job)
        page = self._series_page
        if gen != self._sp_gen or page is None or sip.isdeleted(page):
            return
        log.warning("series page: series_info(%s) failed: %s",
                    (self._sp_ctx or {}).get("series_id"), msg)
        page.set_failed(tr("err_series_open"))

    def _sp_info_in(self, gen: int, client, got, job: int = 0) -> None:
        hub_of(self).finish(job)
        page = self._series_page
        if (gen != self._sp_gen or page is None or sip.isdeleted(page)
                or client is not self.client):
            return
        ok, info = got
        if not ok:
            log.info("series page: not for this profile")
            self._leave_series_page()
            return
        # Whatever the answer, the page never stays loading: an episode list
        # it could not take ends in the page's error, with Retry.
        try:
            self._sp_take_info(client, info)
        except Exception:
            log.exception("series page: the episodes of %s were not shown",
                          (self._sp_ctx or {}).get("series_id"))
            if self._sp_cache:
                self._sp_cache.pop((id(client), str((self._sp_ctx or {}).get("series_id"))), None)
            page.set_failed(tr("err_series_open"))

    def _sp_take_info(self, client, info) -> None:
        """A series_info answer on the page (kept a few minutes)."""
        info = info if isinstance(info, dict) else {}
        series = self._sp_ctx or {}
        sid = series.get("series_id")
        cache = self._sp_cache if self._sp_cache is not None else {}
        cache[(id(client), str(sid))] = (time.monotonic(), info)
        while len(cache) > CACHE_MAX:
            cache.pop(next(iter(cache)))
        self._sp_cache = cache
        # A series opened from a slim snapshot (Continue watching) does not
        # know its category; series_info usually does.
        extra = info.get("info") if isinstance(info.get("info"), dict) else {}
        if series.get("category_id") is None and extra.get("category_id") is not None:
            series["category_id"] = extra["category_id"]
        episodes = self._episode_rows(info, series)
        # The same rows come back from the cache: what was new when the page
        # opened last has been seen since (_follow_entered marks it anew).
        for ep in episodes:
            ep.pop("_new", None)
        first_new = self._follow_entered(sid, info, episodes)
        if not self._sp_focus and first_new is not None:
            self._sp_focus = str(first_new)
        self._sp_put_episodes(episodes, extra)

    def _sp_put_episodes(self, episodes: list, extra: dict) -> None:
        """The episodes on the page, on the season it opens on."""
        page = self._series_page
        if page is None:
            return
        self._sp_label_episodes(episodes)
        seasons = sv.seasons(episodes)
        self._sp_seasons = seasons
        self._sp_fill_header(extra)
        states, nxt = self._sp_compute()
        kept = sv.remembered(self.settings.value(SEASONS_KEY, ""), self._sp_series_key())
        focus = None
        if self._sp_focus:
            focus = next((e for s in seasons for e in s.episodes
                          if sv.episode_key(e) == self._sp_focus), None)
        start = sv.opening_season(seasons, nxt, kept, focus)
        page.set_seasons(seasons, [self._sp_season_label(s) for s in seasons], start)
        self._sp_show_states(states, nxt)
        if focus is not None:
            page.focus_episode(sv.episode_key(focus))
        elif nxt.episode is not None and sv.season_index(seasons, nxt.episode) == start:
            page.focus_episode(sv.episode_key(nxt.episode))
        elif page.model.rowCount():
            page.view.setCurrentIndex(page.model.index(0, 0))
            page.view.setFocus()
        # Again once the page has its size: the card may be further down.
        QTimer.singleShot(0, page.reveal_current)

    def _sp_label_episodes(self, episodes: list) -> None:
        """"E5 · Title" on each card (and "E5" on a card with no picture)."""
        name = self._sp_title()
        for ep in episodes:
            n = sv.episode_number(ep)
            title = sv.episode_title(ep, name)
            short = tr("sp_episode_short", n=n) if n is not None else ""
            ep["_sp_short"] = short
            ep["_sp_label"] = (f"{short} · {title}" if short and title
                               else short or title or tr("misc_episode"))

    @staticmethod
    def _sp_season_label(season: sv.Season) -> tuple[str, str]:
        if season.specials:
            return tr("sp_specials"), tr("sp_specials")
        if season.number is None:
            return tr("sp_episodes"), tr("sp_episodes")
        return (tr("sp_season_chip", n=season.number),
                tr("local_season", n=season.number))

    # -- a local library's series ----------------------------------------------------

    def _open_local_series_page(self, title: str, row: dict | None = None) -> None:
        """A series of the Local files library (its index, read already)."""
        index = getattr(self, "_local_series_index", {}) or {}
        eps = index.get(title)
        if not title or not eps:
            return
        from .mw_local import clean_title, episode_info
        self._sp_local = title
        page = self._sp_show_page()
        self._sp_gen += 1
        self._sp_ctx = {"name": title, "_local": True,
                        "stream_icon": (row or {}).get("stream_icon") or "",
                        "_series_title": title}
        self._sp_focus = ""
        self._sp_next = None
        self._sp_head = None
        page.reset()
        self._sp_fill_header()
        rows: list[dict] = []
        for s_no, e_no, p, stem in sorted(eps):
            r = self._local_file_row(p, stem, stat=False)
            if not r:
                continue
            se = episode_info(stem)
            label = clean_title(stem[se[3]:])[0] if se else ""
            r["name"] = f"E{e_no:02d}" + (f" · {label}" if label else "")
            r["title"] = label
            r["season"], r["episode_num"] = s_no, e_no
            # An episode must not scrobble as a movie under the show's name.
            r["_no_scrobble"] = True
            r["_clean_title"] = ""
            if r.get("stream_icon"):
                r["_sp_still"] = r["stream_icon"]
            rows.append(r)
        lp = getattr(self, "_last_playback", None) or {}
        if lp.get("kind") == "local":
            self._sp_focus = str(lp.get("key") or "")
        self._sp_put_episodes(rows, {})
        page.view.setFocus()

    # -- the header ----------------------------------------------------------------

    def _sp_title(self) -> str:
        ctx = self._sp_ctx or {}
        base = str(ctx.get("name") or ctx.get("title") or "")
        sid = ctx.get("series_id")
        if self._sp_local is None and sid is not None:
            try:
                return self.channel_ov.display_name("series", sid, base)
            except Exception:
                return base
        return base

    def _sp_fill_header(self, extra: dict | None = None) -> None:
        """Title, year, rating, genres, plot, poster and backdrop: the
        source's first (*extra*: its series_info "info"), TMDB's where it
        says nothing. A picture is asked for again only when it changed."""
        page = self._series_page
        if page is None:
            return
        head = self._sp_head
        fresh = head is None
        if head is None:
            head = self._sp_head = {"info": {}, "tmdb": {}, "poster": "",
                                    "backdrop": "", "asked": False}
        if extra is not None:
            head["info"] = dict(extra)
            head["source_in"] = True
        info, td = head["info"], head["tmdb"]
        ctx = self._sp_ctx or {}
        title = self._sp_title()
        if self.tmdb is not None and title and not td and not head["asked"]:
            head["asked"] = True
            got = self.tmdb.get_full(title, "series", partial(self._sp_tmdb_in, self._sp_gen))
            if got:
                head["tmdb"] = td = got
        year = sv.year_of(info.get("releaseDate"), info.get("releasedate"),
                          ctx.get("releaseDate"), ctx.get("year"), td.get("release_date"))
        rating = sv.rating_of(info.get("rating"), ctx.get("rating"), td.get("rating"))
        genres = str(info.get("genre") or ctx.get("genre") or td.get("genres") or "").strip()
        meta = " · ".join(t for t in (year, f"★ {rating:.1f}" if rating else "", genres) if t)
        plot = " ".join(str(info.get("plot") or ctx.get("plot") or td.get("overview")
                            or "").split())
        page.header.set_text(title, meta, plot)
        local = self._sp_local is not None
        sid = ctx.get("series_id")
        if local:
            page.header.set_actions(False)
        else:
            page.header.set_actions(
                True, fav=self.series_favs.is_favorite(sid),
                follow=self.followed.is_followed(sid),
                later=self.is_on_watchlist(ctx, "series"))
        raw = ctx.get("cover") or ctx.get("stream_icon") or info.get("cover")
        try:
            poster = (self.cover.cover_url(ctx, "series") if not local
                      else None) or tmdb_url_from_provider(raw) or raw
        except Exception:
            poster = raw
        poster = str(td.get("poster_url") or poster or "")
        backdrop = sv.backdrop_of(info.get("backdrop_path"), ctx.get("backdrop_path"),
                                  td.get("backdrop_url"))
        gen = self._sp_gen
        if fresh:
            page.header.set_poster(None, title)
            page.header.set_backdrop(None)
        if poster != head["poster"] or fresh:
            head["poster"] = poster
            if poster:
                self.poster_art.get(poster, partial(self._sp_poster_in, gen, poster))
        if backdrop != head["backdrop"] or fresh:
            head["backdrop"] = backdrop
            if backdrop:
                self._sp_art().get(backdrop, partial(self._sp_backdrop_in, gen, backdrop))
        # A title TMDB matched before backdrops were kept (no "backdrop_url"
        # in its entry), the source giving none either: asked for again by
        # its id, once, off the window's thread (_sp_tmdb_in draws it).
        fill = getattr(self.tmdb, "fill_backdrop", None)
        if (callable(fill) and td and "backdrop_url" not in td and not backdrop
                and head.get("source_in") and not head.get("backdrop_asked")):
            head["backdrop_asked"] = True
            fill(title, "series", partial(self._sp_tmdb_in, gen))

    def _sp_tmdb_in(self, gen: int, details) -> None:
        page = self._series_page
        if gen != self._sp_gen or page is None or sip.isdeleted(page):
            return
        if self._sp_head is not None:
            self._sp_head["tmdb"] = details if isinstance(details, dict) else {}
        self._sp_fill_header(None)
        self._sp_season_shown(page.current)

    def _sp_poster_in(self, gen: int, url: str, pm: QPixmap) -> None:
        page = self._series_page
        if (gen != self._sp_gen or page is None or sip.isdeleted(page)
                or (self._sp_head or {}).get("poster") != url):
            return
        if pm is not None and not pm.isNull():
            page.header.set_poster(pm, self._sp_title())
            if not page.header.has_backdrop():
                page.set_fallback_art(pm)

    def _sp_backdrop_in(self, gen: int, url: str, pm: QPixmap) -> None:
        page = self._series_page
        if (gen != self._sp_gen or page is None or sip.isdeleted(page)
                or (self._sp_head or {}).get("backdrop") != url):
            return
        if pm is not None and not pm.isNull():
            page.header.set_backdrop(pm)
            page.set_fallback_art(pm)

    # -- what each episode says ------------------------------------------------------

    def _sp_series_key(self) -> str:
        if self._sp_local is not None:
            return f"local:{self._sp_local}"
        store = getattr(self, "playlist_store", None)
        pid = getattr(store, "active_id", None) or ""
        return f"{pid}:{(self._sp_ctx or {}).get('series_id')}"

    def _series_page_playing(self) -> bool:
        """The series page is up and one of its episodes plays (the poster
        under the video then pauses and resumes it)."""
        try:
            page = getattr(self, "_series_page", None)
            return bool(page is not None and not sip.isdeleted(page)
                        and self._series_page_showing() and self._sp_playing()[0])
        except Exception:
            return False

    def _sp_playing(self) -> tuple[str, float]:
        """(key, how far) of what plays when it is one of this page's."""
        lp = getattr(self, "_last_playback", None) or {}
        key = getattr(self, "_playing_key", None)
        if key is None:
            return "", 0.0
        if self._sp_local is not None:
            if lp.get("kind") != "local":
                return "", 0.0
        else:
            ctx = lp.get("series_ctx") or {}
            if (lp.get("kind") != "episode" or str(ctx.get("series_id"))
                    != str((self._sp_ctx or {}).get("series_id"))):
                return "", 0.0
        p = getattr(self, "player", None)
        frac = 0.0
        try:
            if p is not None and p.playback_duration() > 0:
                frac = p.playback_position() / p.playback_duration()
        except Exception:
            frac = 0.0
        return str(key), max(0.0, min(1.0, frac))

    def _sp_compute(self) -> tuple[dict[str, EpisodeState], sv.NextUp]:
        """Every episode's state and the one to watch next."""
        local = self._sp_local is not None
        kind = "local" if local else "episode"
        states: dict[str, EpisodeState] = {}
        all_eps = [e for s in self._sp_seasons for e in s.episodes]
        saved = self.series_ctx
        self.series_ctx = None if local else self._sp_ctx
        try:
            for ep in all_eps:
                key = sv.episode_key(ep)
                watched = None if local else self.watched_source(ep, "episode")
                pos, dur, at = self.resume.progress(self._item_key(ep), kind)
                if not local:
                    srv = self._ms_resume_at(self._item_key(ep), "episode", ep, pos)
                    if srv != pos:
                        pos, at = srv, max(at, int(ep.get("_last_played") or 0))
                if dur <= 0:
                    dur = sv.minutes_of(ep) * 60.0
                states[key] = EpisodeState(watched, sv.Progress(pos, dur, at),
                                           bool(ep.get("_new")))
        finally:
            self.series_ctx = saved
        order = sv.flow(self._sp_seasons)
        nxt = sv.next_up(order, lambda e: bool(states[sv.episode_key(e)].watched),
                         lambda e: states[sv.episode_key(e)].progress)
        self._sp_next = nxt
        return states, nxt

    def _sp_show_states(self, states: dict[str, EpisodeState], nxt: sv.NextUp) -> None:
        page = self._series_page
        if page is None:
            return
        playing, frac = self._sp_playing()
        next_key = sv.episode_key(nxt.episode) if nxt.episode is not None else ""
        page.set_states(states, next_key, playing, frac)
        ep = nxt.episode
        if ep is None:
            page.header.set_primary("", None, "")
            return
        what = self._sp_se(ep)
        text = tr("sp_play" if nxt.first else "sp_continue", episode=what)
        left = (tr("home_min_left", n=nxt.progress.minutes_left)
                if nxt.resume and nxt.progress.minutes_left else "")
        page.header.set_primary(text, nxt.progress.fraction if nxt.resume else None, left)

    @staticmethod
    def _sp_se(ep: dict) -> str:
        s, e = sv.season_of(ep.get("season")), sv.episode_number(ep)
        if s is not None and e is not None:
            return tr("sp_se", s=s, e=e)
        if e is not None:
            return tr("sp_episode_short", n=e)
        return str(ep.get("_sp_label") or ep.get("name") or "")

    def _sp_refresh(self) -> None:
        """The marks, progress and next episode drawn again."""
        page = self._series_page
        if page is None or sip.isdeleted(page) or not self._sp_seasons:
            return
        try:
            states, nxt = self._sp_compute()
            self._sp_show_states(states, nxt)
            if self._sp_local is None:
                ctx = self._sp_ctx or {}
                sid = ctx.get("series_id")
                page.header.set_actions(
                    True, fav=self.series_favs.is_favorite(sid),
                    follow=self.followed.is_followed(sid),
                    later=self.is_on_watchlist(ctx, "series"))
        except Exception:
            log.exception("series page: the marks were not drawn again")

    def _sp_refresh_soon(self) -> None:
        if self._series_page is not None:
            QTimer.singleShot(0, self._sp_refresh)

    def _sp_tick(self) -> None:
        if not self._series_page_visible():
            if self._sp_timer is not None and not self._series_page_showing():
                self._sp_timer.stop()
            return
        if self._sp_playing()[0]:
            self._sp_refresh()

    def _sp_playback_started(self, key, kind: str) -> None:
        """_start_playback: a title started. One of this page's: marked,
        and the page goes to it (autoplay's next episode too)."""
        page = self._series_page
        if page is None or sip.isdeleted(page) or not self._sp_seasons:
            return
        try:
            playing, _frac = self._sp_playing()
            if playing and self._series_page_showing():
                page.focus_episode(playing, take_focus=False)
            self._sp_refresh()
            self._apply_play_icon()
        except Exception:
            log.exception("series page: what plays was not marked")

    # -- the seasons ------------------------------------------------------------------

    def _sp_season_picked(self, i: int) -> None:
        """A season the person picked: kept for the next time (never while
        viewing privately), with the episode that is next now."""
        if privacy.private() or not 0 <= i < len(self._sp_seasons):
            return
        nxt = self._sp_next
        next_key = sv.episode_key(nxt.episode) if nxt and nxt.episode is not None else ""
        self.settings.setValue(SEASONS_KEY, sv.remember(
            self.settings.value(SEASONS_KEY, ""), self._sp_series_key(),
            self._sp_seasons[i].number, next_key))

    def _sp_season_shown(self, i: int) -> None:
        """A season on show: TMDB's pictures for its episodes the source
        has none of, asked for once, off the window's thread."""
        if self.tmdb is None or not 0 <= i < len(self._sp_seasons):
            return
        season = self._sp_seasons[i]
        if season.number is None or not any(not sv.still_of(e) for e in season.episodes):
            return
        tid = self.tmdb.tmdb_id_for(self._sp_title(), "series")
        if not isinstance(tid, int):
            return
        known = self._sp_tmdb if self._sp_tmdb is not None else {}
        self._sp_tmdb = known
        key = (tid, season.number)
        if key in known:
            if known[key]:
                self._sp_stills_in(self._sp_gen, key, known[key])
            return
        known[key] = {}
        run_async(self.pool, partial(self.tmdb.client.season_episodes, tid, season.number),
                  partial(self._sp_stills_in, self._sp_gen, key),
                  partial(self._sp_stills_failed, key))

    def _sp_stills_failed(self, key, msg) -> None:
        log.debug("series page: no TMDB season %s: %s", key, msg)
        if self._sp_tmdb is not None:
            self._sp_tmdb.pop(key, None)

    def _sp_stills_in(self, gen: int, key, got) -> None:
        if self._sp_tmdb is not None:
            self._sp_tmdb[key] = got or {}
        page = self._series_page
        if gen != self._sp_gen or page is None or sip.isdeleted(page) or not got:
            return
        for season in self._sp_seasons:
            if season.number != key[1]:
                continue
            for ep in season.episodes:
                hit = got.get(sv.episode_number(ep))
                if not hit:
                    continue
                if not sv.still_of(ep) and hit.get("still"):
                    ep["_sp_still"] = hit["still"]
                if not sv.plot_of(ep) and hit.get("overview"):
                    ep["_sp_plot"] = hit["overview"]
                if not sv.minutes_of(ep) and hit.get("runtime"):
                    ep["_sp_runtime"] = str(hit["runtime"])
        page.delegate.forget_text()
        page.view.viewport().update()

    # -- playing ---------------------------------------------------------------------

    def _sp_take_start(self, key) -> float | None:
        """_start_playback: where Continue starts its episode - without
        the resume question (once, for that episode only)."""
        once, self._sp_start_once = self._sp_start_once, None
        if once is not None and str(once[0]) == str(key):
            return float(once[1])
        return None

    def _sp_primary(self) -> None:
        nxt = self._sp_next
        if nxt is None or nxt.episode is None:
            return
        at = nxt.progress.pos if nxt.resume and nxt.progress.pos > 0 else None
        self._sp_play(nxt.episode, at)

    def _sp_queue(self, ep: dict) -> list[dict]:
        """What autoplay goes on to after *ep*: its seasons in order (the
        Specials among themselves)."""
        season = next((s for s in self._sp_seasons if any(e is ep for e in s.episodes)), None)
        return sv.flow(self._sp_seasons, specials=bool(season and season.specials))

    def _sp_own(self, ep: dict) -> dict:
        """The page's own row of *ep* (a copy may have come by a signal):
        autoplay's queue finds the episode by identity."""
        key = sv.episode_key(ep)
        for s in self._sp_seasons:
            for e in s.episodes:
                if e is ep:
                    return e
        for s in self._sp_seasons:
            for e in s.episodes:
                if key and sv.episode_key(e) == key:
                    return e
        return ep

    def _sp_play(self, ep, at: float | None = None) -> None:
        """An episode in the docked player; the page stays. *at*: where it
        starts, without the resume question (Continue)."""
        if not isinstance(ep, dict):
            return
        ep = self._sp_own(ep)
        key = self._item_key(ep)
        p = getattr(self, "player", None)
        # The same id of another kind (a live channel's) is another title:
        # ids are unique only within a type.
        group = "local" if self._sp_local is not None else "episode"
        if (key is not None and str(key) == str(getattr(self, "_playing_key", None))
                and getattr(self, "_playing_group", None) == group
                and p is not None and getattr(p, "current_url", None)):
            return                      # it plays already: nothing to start over
        if self._sp_local is not None:
            self._sp_play_local(ep, at)
            return
        self._play_series_episode(self._sp_ctx or {}, self._sp_seasons, ep, at)

    def _play_series_episode(self, series: dict, seasons: list, ep: dict,
                             at: float | None = None) -> bool:
        """A source's episode *ep* of *series* in the docked player, with
        the series as its context for the moment - resume, marks, Trakt and
        Simkl, History, and autoplay's queue (*seasons* in order, the
        Specials among themselves). *at*: where it starts, without the
        resume question. The page plays through here, and the phone's
        Episodes view (mw_phone_browse). False when nothing could start."""
        if ep.get("id") is None:
            return False
        if self._links_later(partial(self._sp_episode_url, ep),
                             partial(self._play_series_episode, series, seasons, ep, at)):
            return True
        url = self._sp_episode_url(ep)
        if not url:
            self._set_status(tr("err_series_open"), error=True)
            return False
        key = self._item_key(ep)
        season = next((s for s in seasons if any(e is ep for e in s.episodes)), None)
        queue = sv.flow(seasons, specials=bool(season and season.specials))
        saved = self.series_ctx
        self.series_ctx = series
        try:
            self._ep_queue_override = queue
            self._ep_index_override = next((i for i, e in enumerate(queue) if e is ep), -1)
            if at is not None:
                self._sp_start_once = (key, at)
            self._start_playback(url, ep.get("name") or ep.get("title") or "dopeIPTV",
                                 ep.get("stream_icon") or ep.get("cover"), key,
                                 "episode", record=True, item=ep)
        finally:
            self.series_ctx = saved
            self._sp_start_once = None
            # Taken by _start_playback - unless it turned back (the
            # recordings' question, the VPN guard): then never left for the
            # next episode played from elsewhere.
            self._ep_queue_override = None
            self._ep_index_override = -1
        return True

    def _sp_episode_url(self, ep: dict) -> str:
        return self.client.episode_url(ep.get("id"), ep.get("container_extension")) or ""

    def _sp_play_local(self, ep: dict, at: float | None = None) -> None:
        import os
        path = ep.get("_path")
        if not path or not os.path.isfile(path):
            return
        q = getattr(self, "_track_queue", []) or []
        if not (q and getattr(self, "_queue_explicit", False)):
            # The series plays on, episode after episode (the play queue).
            self._track_queue = self._sp_queue(ep)
            self._queue_explicit = False
        if at is not None:
            self._sp_start_once = (path, at)
        try:
            self._start_playback(path, ep.get("name") or os.path.basename(path),
                                 ep.get("stream_icon"), path, "local", record=True, item=ep)
        finally:
            self._sp_start_once = None

    def _sp_play_external(self, ep: dict, player: str) -> None:
        if self._sp_local is not None:
            path = ep.get("_path")
            if path:
                launch_player(player, path, ep.get("name") or "", self)
            return
        # A portal's address is asked for off the window's thread (as the
        # list's Open externally does).
        if self._links_later(partial(self._sp_episode_url, ep),
                             partial(self._sp_play_external, ep, player)):
            return
        url = self._sp_episode_url(ep)
        if not url or not self._confirm_external_while_playing() \
                or self._vpn_guard_blocks(url):
            return
        title = ep.get("name") or ep.get("title") or "dopeIPTV"
        launch_player(player, url, title, self)
        saved = self.series_ctx
        self.series_ctx = self._sp_ctx
        try:
            self.history.add(url, title, ep.get("stream_icon"), self._item_key(ep), "episode",
                             extra=self._history_extra("episode", ep, title))
        finally:
            self.series_ctx = saved

    def _sp_with_series(self, fn, *args) -> None:
        """*fn*(*args) with the page's series as the episodes' context (the
        marks are filed under it), the page drawn again after."""
        saved = self.series_ctx
        self.series_ctx = None if self._sp_local is not None else self._sp_ctx
        try:
            fn(*args)
        finally:
            self.series_ctx = saved
        self._sp_refresh()

    def _sp_copy_url(self, ep: dict) -> None:
        """Copy stream URL - a portal's asked for only now, off the
        window's thread; a local file's path."""
        if self._sp_local is not None:
            url = ep.get("_path") or ""
        elif self._links_later(partial(self._sp_episode_url, ep),
                               partial(self._sp_copy_url, ep), key="copy"):
            return
        else:
            url = self._sp_episode_url(ep)
        if url:
            QApplication.clipboard().setText(url)

    def _sp_forget_resume(self, ep: dict) -> None:
        kind = "local" if self._sp_local is not None else "episode"
        self.resume.clear(kind, self._item_key(ep))
        if kind == "episode":
            self._ms_forget(ep)
        self._sp_refresh()

    # -- the menus -------------------------------------------------------------------

    def _sp_episode_menu(self, ep, pos: QPoint) -> None:
        """Right-click on an episode: what the list offered for one."""
        if not isinstance(ep, dict):
            return
        ep = self._sp_own(ep)
        m = QMenu(self)
        m.addAction(tr("ctx_play_in_mpv"), partial(self._sp_play, ep))
        ext = m.addMenu(tr("ctx_open_externally"))
        ext.addAction("mpv", partial(self._sp_play_external, ep, "mpv"))
        ext.addAction("VLC", partial(self._sp_play_external, ep, "vlc"))
        m.addAction(tr("ctx_cast_to_chromecast"),
                    partial(self._sp_with_series, self._open_cast_dialog, ep))
        page = self._series_page
        state = (page.states.get(sv.episode_key(ep)) if page is not None else None) \
            or EpisodeState()
        if state.progress.started:
            m.addAction(tr("ctx_continue_remove"), partial(self._sp_forget_resume, ep))
        if self._sp_local is None:
            m.addSeparator()
            self._add_follow_action(m, self._sp_ctx or {})
            m.addSeparator()
            trakt_ok = bool(self._watch_accounts()) and (
                self.tmdb is not None or isinstance(ep.get("_tmdb_id"), int))
            if state.watched:
                m.addAction(tr("ctx_unmark_watched"), partial(
                    self._sp_with_series, self._unmark_episode_watched, ep, False))
                if trakt_ok:
                    m.addAction(self._account_action("ctx_unmark_watched"), partial(
                        self._sp_with_series, self._unmark_episode_watched, ep, True))
            else:
                m.addAction(tr("ctx_mark_watched"), partial(
                    self._sp_with_series, self._mark_episode_watched, ep, False))
                if trakt_ok:
                    m.addAction(self._account_action("ctx_mark_watched"), partial(
                        self._sp_with_series, self._mark_episode_watched, ep, True))
        m.addSeparator()
        m.addAction(tr("ctx_copy_stream_url"), partial(self._sp_copy_url, ep))
        m.exec(pos)
        self._sp_refresh()

    def _sp_more_menu(self, pos: QPoint) -> None:
        """The header's ⋯: the series' own actions the list's menu had."""
        ctx = self._sp_ctx or {}
        if self._sp_local is not None or ctx.get("series_id") is None:
            return
        m = QMenu(self)
        trakt_ok = bool(self._watch_accounts()) and self.tmdb is not None
        if self.show_watched_count(ctx) > 0:
            m.addAction(tr("ctx_unmark_watched"), partial(
                self._sp_with_series, self._unmark_series_watched, ctx, False))
            if trakt_ok:
                m.addAction(self._account_action("ctx_unmark_watched"), partial(
                    self._sp_with_series, self._unmark_series_watched, ctx, True))
        else:
            m.addAction(tr("ctx_mark_watched"), partial(
                self._sp_with_series, self._mark_series_watched, ctx, False))
            if trakt_ok:
                m.addAction(self._account_action("ctx_mark_watched"), partial(
                    self._sp_with_series, self._mark_series_watched, ctx, True))
        if trakt_ok:
            m.addSeparator()
            if self.is_on_watchlist(ctx, "series"):
                m.addAction(self._account_action("ctx_watchlist_remove"), partial(
                    self._sp_with_series, self._remove_watchlist, ctx, "series", True))
            else:
                m.addAction(self._account_action("ctx_watchlist_add"), partial(
                    self._sp_with_series, self._add_watchlist, ctx, "series", True))
        m.addSeparator()
        self._add_fav_folder_menu(m, "series", [ctx], self.series_favs)
        if self.tmdb:
            m.addAction(tr("ctx_match_tmdb"), partial(self._sp_match_tmdb, ctx))
        m.exec(pos)
        self._sp_refresh()

    def _sp_match_tmdb(self, ctx: dict) -> None:
        self._open_tmdb_match_dialog(ctx, "series")
        if self._sp_head is not None:
            self._sp_head.update(tmdb={}, asked=False)
        self._sp_fill_header(None)

    def _sp_toggle_fav(self, on: bool) -> None:
        ctx = self._sp_ctx or {}
        if ctx.get("series_id") is not None:
            self._toggle_media_fav(ctx, "series", bool(on))
        self._sp_refresh()

    def _sp_toggle_follow(self, on: bool) -> None:
        ctx = self._sp_ctx or {}
        if ctx.get("series_id") is None:
            return
        if on:
            self._follow_series(ctx)
        else:
            self._unfollow_series(ctx)
        self._sp_refresh()

    def _sp_toggle_later(self, on: bool) -> None:
        ctx = self._sp_ctx or {}
        if ctx.get("series_id") is None:
            return
        if on:
            self._add_watchlist(ctx, "series", False)
        else:
            self._remove_watchlist(ctx, "series", False)
        self._sp_refresh()

    # -- leaving it ------------------------------------------------------------------

    def _close_series_page(self) -> None:
        """The list back in the middle pane (the page kept, hidden)."""
        self._sp_return_search = False
        if self._sp_timer is not None:
            self._sp_timer.stop()
        if self._series_page_showing():
            self._mid_stack.setCurrentIndex(0)

    def _leave_series_page(self) -> None:
        """Back where you were: the Search page when the page was opened
        from there, else the place under it, as it was, with its entry
        lit."""
        if not self._series_page_showing():
            return
        search = self._sp_return_search
        self._close_series_page()
        if search:
            self._sa_back_from_series()
            return
        self._return_to_place()

    def _sp_category_clicked(self, _item=None) -> None:
        """A category picked in the sidebar: its list, in the page's place."""
        if self._series_page_showing():
            self._close_series_page()
            self._check_nav(self._nav_key_for_mode(self.mode))

    def _sp_source_changed(self) -> None:
        """Another source (or profile): this series is not its own."""
        self._sp_gen += 1
        self._sp_cache = None
        self._sp_tmdb = None
        if self._series_page_showing():
            self._close_series_page()
            self._check_nav(self._nav_key_for_mode(self.mode))
        page = self._series_page
        if page is not None:
            self._sp_seasons = []
            self._sp_ctx = None
            self._sp_local = None
            page.reset()

    def _sp_restyle(self) -> None:
        page = self._series_page
        if page is None:
            return
        page.restyle()
        page.retranslate()
        if self._sp_seasons:
            page.season_bar.set_seasons([self._sp_season_label(s) for s in self._sp_seasons],
                                        page.current)
            self._sp_fill_header(None)
            self._sp_label_episodes([e for s in self._sp_seasons for e in s.episodes])
            self._sp_refresh()

    # -- for TV mode -----------------------------------------------------------------

    def _sp_episode_progress(self, ep: dict, series: dict) -> tuple[bool, sv.Progress]:
        """(watched, how far) of a source's episode of *series* - what TV
        mode's own series screen shows, the same as the page's."""
        saved = self.series_ctx
        self.series_ctx = series
        try:
            watched = bool(self.watched_source(ep, "episode"))
            key = self._item_key(ep)
            pos, dur, at = self.resume.progress(key, "episode")
            srv = self._ms_resume_at(key, "episode", ep, pos)
            if srv != pos:
                pos = srv
            if dur <= 0:
                dur = sv.minutes_of(ep) * 60.0
            return watched, sv.Progress(pos, dur, at)
        except Exception:
            return False, sv.Progress()
        finally:
            self.series_ctx = saved


def _fetch(client, sid, series: dict, keep) -> tuple[bool, dict]:
    """On a worker: the series' episodes - and, for a kids profile, whether
    its category may be shown at all (the categories read here if need be)."""
    ok = True
    if keep is not None:
        keep.ready()
        ok = bool(keep(series))
    if not ok:
        return False, {}
    return True, client.series_info(sid) or {}

