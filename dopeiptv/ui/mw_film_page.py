"""MainWindow mixin: a film's own page in the middle pane, over the Movies
place it was opened from (ui/film_page.py draws it, core/film_view.py
works out what it says).

It opens from a film's card - the grid or the list, Popular right now,
Continue (ui/mw_place_films.py). What it shows comes from what the window
always had: the row itself at once, the source's vod_info off the window's
thread (the detail panel's own cache, so a film looked at there is not
asked for again), TMDB's details through the poster resolver - the cast
only from those two, nothing asked for to find it.

The place under the page is never touched: Back (its arrow, Esc, the
mouse's back button) shows it again exactly as it was - category, scroll,
the card picked. A category or a nav entry picked meanwhile takes the page
away too.

The film plays in the docked player and the page stays, through the
window's own path (_start_playback, as the list's double-click does), so
resume points, watched marks, Trakt and Simkl, History, the subtitles
remembered for it and a media server's reports all go as before; Continue
starts where it stopped without the question. Opening the page never plays
anything - a film never starts by being picked, as in the lists.

A kids profile never opens a film for adults, a locked category's asks for
the PIN (never a kids profile's); the page keeps nothing of its own.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QTimer
from PyQt6.QtGui import QPixmap

from ..core import film_view as fv
from ..core.log import log
from ..core.profiles import adult_item
from ..core.workers import run_async, tmdb_url_from_provider
from ..i18n import tr
from .film_page import FilmPage
from .mw_pane_follow import from_pane
from .series_cards import minutes_text

#: How often the button of what plays is brought up to date.
TICK_MS = 10_000


class _FilmPageMixin:
    """MainWindow mixin: a film's page."""

    _film_page: FilmPage | None = None
    _fp_row: dict | None = None
    _fp_gen = 0
    _fp_head: dict | None = None
    _fp_timer: QTimer | None = None

    # -- where it is ------------------------------------------------------------------

    def _film_page_showing(self) -> bool:
        page = self._film_page
        stack = getattr(self, "_mid_stack", None)
        return page is not None and stack is not None and stack.currentWidget() is page

    def _film_page_visible(self) -> bool:
        return self._film_page_showing() and self._film_page.isVisible()

    def _fp_key(self) -> str:
        row = self._fp_row
        if not row:
            return ""
        key = self._item_key(row)
        return "" if key is None else str(key)

    def _film_page_shows(self, key: Any) -> bool:
        """The page is up with the film of *key*."""
        return (self._film_page_showing() and key is not None
                and bool(self._fp_key()) and self._fp_key() == str(key))

    def _ensure_film_page(self) -> FilmPage:
        page = self._film_page
        if page is None:
            page = FilmPage()
            page.back_requested.connect(self._leave_film_page)
            h = page.header
            h.primary.connect(self._fp_primary)
            h.favorite.connect(self._fp_toggle_fav)
            h.later.connect(self._fp_toggle_later)
            h.more.connect(self._fp_more_menu)
            page.cast.member_clicked.connect(self._fp_cast_clicked)
            self._film_page = page
            self._mid_stack.addWidget(page)
            t = QTimer(self)
            t.setInterval(TICK_MS)
            t.timeout.connect(self._fp_tick)
            self._fp_timer = t
        return page

    # -- opening it -------------------------------------------------------------------

    def _fp_allowed(self, row: dict) -> bool:
        """The parental lock and a kids profile, as the lists have them."""
        kids = getattr(self, "_profile_kids", lambda: False)()
        if kids and (adult_item(row)
                     or not getattr(self, "_kids_allows", lambda _r, _m: True)(row, "vod")):
            return False
        cat = row.get("category_id")
        if (cat is not None and self.overrides.is_locked("vod", cat)
                and not self.parental.session_unlocked):
            if kids or not self._request_unlock():
                return False
        return True

    def _open_film_page(self, row: Any, back: str = "") -> bool:
        """*row*'s page (a film row: its stream id, or a Continue row's own
        address), over what the middle pane shows; *back*: the words of the
        list Back goes to ("Back to Action"). False when it may not open."""
        if not isinstance(row, dict) or (row.get("stream_id") is None and not row.get("_url")):
            return False
        if not self._fp_allowed(row):
            log.info("film page: not for this profile or locked")
            return False
        page = self._ensure_film_page()
        same = self._film_page_showing() and self._fp_row is row
        self._close_guide_page()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        self._mid_stack.setCurrentWidget(page)
        page.set_back_text(back)
        if self._fp_timer is not None:
            self._fp_timer.start()
        if same:
            return True
        self._fp_gen += 1
        self._fp_row = row
        self._fp_head = {"info": {}, "tmdb": {}, "poster": "", "backdrop": "",
                         "asked": False, "info_in": False, "photos": {}}
        page.reset()
        self._fp_fill()
        self._fp_ask_info()
        self._fp_ask_tmdb()
        page.header.play_btn.setFocus()
        return True

    def _fp_ask_info(self) -> None:
        """The source's vod_info (plot, genre, length, cast, backdrop) off
        the window's thread - the detail panel's cache first."""
        row, page, gen = self._fp_row or {}, self._film_page, self._fp_gen
        sid = row.get("stream_id")
        head = self._fp_head or {}
        fetch = getattr(self.client, "vod_info", None)
        if page is None or sid is None or fetch is None:
            head["info_in"] = True
            return
        cached = self._info_cache.get(("vod", sid))
        if cached is not None:
            self._fp_info_in(gen, sid, cached)
            return
        page.set_loading(True)
        client = self.client

        def work():
            got = client.vod_info(sid) or {}
            return got.get("info") if isinstance(got, dict) else {}

        run_async(self.pool, work, partial(self._fp_info_in, gen, sid),
                  partial(self._fp_info_failed, gen, sid))

    def _fp_info_failed(self, gen: int, sid, msg) -> None:
        log.info("film page: vod_info(%s) failed: %s", sid, msg)
        self._fp_info_in(gen, sid, {}, keep=False)

    def _fp_info_in(self, gen: int, sid, info, keep: bool = True) -> None:
        page = self._film_page
        if gen != self._fp_gen or page is None or sip.isdeleted(page):
            return
        info = info if isinstance(info, dict) else {}
        if keep:
            cache = self._info_cache
            cache[("vod", sid)] = info
            while len(cache) > 250:
                cache.pop(next(iter(cache)))
        head = self._fp_head or {}
        head["info"] = info
        head["info_in"] = True
        page.set_loading(False)
        self._fp_fill()

    def _fp_ask_tmdb(self) -> None:
        tmdb = getattr(self, "tmdb", None)
        head = self._fp_head
        if tmdb is None or head is None or head.get("asked"):
            return
        head["asked"] = True
        title = str((self._fp_row or {}).get("name") or (self._fp_row or {}).get("title") or "")
        if not title:
            return
        got = tmdb.get_full(title, "vod", partial(self._fp_tmdb_in, self._fp_gen))
        if got:
            head["tmdb"] = got
            self._fp_fill()

    def _fp_tmdb_in(self, gen: int, details) -> None:
        page = self._film_page
        if gen != self._fp_gen or page is None or sip.isdeleted(page) or self._fp_head is None:
            return
        self._fp_head["tmdb"] = details if isinstance(details, dict) else {}
        self._fp_fill()

    # -- what it says -----------------------------------------------------------------

    def _fp_title(self) -> str:
        row = self._fp_row or {}
        base = str(row.get("name") or row.get("title") or "")
        key = self._item_key(row)
        if key is None:
            return base
        try:
            return self.channel_ov.display_name("vod", key, base)
        except Exception:
            return base

    def _fp_progress(self) -> tuple[float, float]:
        """(where it stopped, its length) in seconds - this computer's
        resume point or a media server's newer one."""
        row = self._fp_row or {}
        key = self._item_key(row)
        if key is None:
            return 0.0, 0.0
        pos, dur, _at = self.resume.progress(key, "movie")
        srv = self._ms_resume_at(key, "movie", row, pos)
        if srv != pos:
            pos = srv
        if pos <= 0 and row.get("_resume_pos"):
            pos = float(row.get("_resume_pos") or 0)
            dur = dur or float(row.get("_resume_dur") or 0)
        return float(pos or 0.0), float(dur or 0.0)

    def _fp_fill(self) -> None:
        """Title, year · length · genres · rating, plot, poster, backdrop,
        cast, the button and the actions: the source's first, TMDB's where
        it says nothing. A picture is asked for again only when it changed."""
        page, row, head = self._film_page, self._fp_row or {}, self._fp_head
        if page is None or head is None:
            return
        info, td = head["info"], head["tmdb"]
        title = self._fp_title()
        pos, dur = self._fp_progress()
        mins = fv.minutes_of(row, info, dur_secs=dur)
        year = fv.year_of(row, info, td)
        rating = fv.rating_of(row, info, td)
        genres = fv.genres_of(info, row, td)
        meta = " · ".join(t for t in (year, minutes_text(mins) if mins else "", genres,
                                      f"★ {rating:.1f}" if rating else "") if t)
        page.header.set_text(title, meta, fv.plot_of(info, row, td))
        self._fp_show_primary(pos, dur, mins)
        self._fp_show_actions()
        gen = self._fp_gen
        raw = row.get("stream_icon") or row.get("cover") or info.get("movie_image") \
            or info.get("cover_big")
        try:
            poster = self.cover.cover_url(row, "vod") or tmdb_url_from_provider(raw) or raw
        except Exception:
            poster = raw
        poster = str(poster or td.get("poster_url") or "")
        if not head["poster"] and not poster:
            page.header.set_poster(None, title)
        if poster and poster != head["poster"]:
            if not head["poster"]:
                page.header.set_poster(None, title)
            head["poster"] = poster
            self.poster_art.get(poster, partial(self._fp_poster_in, gen, poster))
        from ..core import series_view as sv
        backdrop = sv.backdrop_of(info.get("backdrop_path"), row.get("backdrop_path"),
                                  td.get("backdrop_url"))
        if backdrop and backdrop != head["backdrop"]:
            head["backdrop"] = backdrop
            self._sp_art().get(backdrop, partial(self._fp_backdrop_in, gen, backdrop))
        fill = getattr(getattr(self, "tmdb", None), "fill_backdrop", None)
        if (callable(fill) and td and "backdrop_url" not in td and not backdrop
                and head.get("info_in") and not head.get("backdrop_asked")):
            head["backdrop_asked"] = True
            fill(str(row.get("name") or row.get("title") or ""), "vod",
                 partial(self._fp_tmdb_in, gen))
        self._fp_show_cast(fv.cast_of(info, td))

    def _fp_show_primary(self, pos: float, dur: float, mins: int) -> None:
        page = self._film_page
        if page is None:
            return
        state = fv.primary(pos, dur, mins)
        if state.resume:
            left = state.progress.minutes_left
            page.header.set_primary(tr("card_continue"), state.progress.fraction,
                                    tr("home_min_left", n=left) if left else "")
        else:
            page.header.set_primary(tr("btn_play"), None, "")

    def _fp_show_actions(self) -> None:
        page, row = self._film_page, self._fp_row or {}
        if page is None:
            return
        sid = row.get("stream_id")
        page.header.set_actions(sid is not None,
                                fav=self.movie_favs.is_favorite(sid) if sid is not None else False,
                                later=self.is_on_watchlist(row, "vod"))

    def _fp_show_cast(self, members: list) -> None:
        page, head = self._film_page, self._fp_head
        if page is None or head is None:
            return
        if [m[0] for m in members] == page.cast.names() and head.get("cast_set"):
            return
        head["cast_set"] = True
        page.cast.set_cast(members, getattr(self, "tmdb", None) is not None)
        gen = self._fp_gen
        for i, (_name, url, _pid) in enumerate(members):
            if url:
                self.poster_art.get(url, partial(self._fp_photo_in, gen, i))

    def _fp_poster_in(self, gen: int, url: str, pm: QPixmap) -> None:
        page = self._film_page
        if (gen != self._fp_gen or page is None or sip.isdeleted(page)
                or (self._fp_head or {}).get("poster") != url):
            return
        if pm is not None and not pm.isNull():
            page.header.set_poster(pm, self._fp_title())

    def _fp_backdrop_in(self, gen: int, url: str, pm: QPixmap) -> None:
        page = self._film_page
        if (gen != self._fp_gen or page is None or sip.isdeleted(page)
                or (self._fp_head or {}).get("backdrop") != url):
            return
        if pm is not None and not pm.isNull():
            page.header.set_backdrop(pm)

    def _fp_photo_in(self, gen: int, index: int, pm: QPixmap) -> None:
        page = self._film_page
        if gen != self._fp_gen or page is None or sip.isdeleted(page):
            return
        page.cast.set_photo(index, pm)

    def _fp_refresh(self) -> None:
        """The button and the actions drawn again (a mark, a resume point,
        a favourite)."""
        page = self._film_page
        if page is None or sip.isdeleted(page) or self._fp_row is None:
            return
        try:
            head = self._fp_head or {}
            pos, dur = self._fp_progress()
            self._fp_show_primary(pos, dur, fv.minutes_of(self._fp_row, head.get("info"),
                                                          dur_secs=dur))
            self._fp_show_actions()
        except Exception:
            log.exception("film page: not drawn again")

    def _fp_refresh_soon(self) -> None:
        if self._film_page is not None:
            QTimer.singleShot(0, self._fp_refresh)

    def _fp_tick(self) -> None:
        if not self._film_page_visible():
            if self._fp_timer is not None and not self._film_page_showing():
                self._fp_timer.stop()
            return
        if self._film_page_shows(getattr(self, "_playing_key", None)):
            self._fp_refresh()

    def _fp_playback_started(self, key, kind: str) -> None:
        """_start_playback: a title started - the page's button drawn
        again (a film played from here now plays; another one played)."""
        if self._film_page is not None and self._fp_row is not None:
            self._fp_refresh_soon()

    # -- playing ---------------------------------------------------------------------

    def _fp_primary(self) -> None:
        row = self._fp_row
        if row is None:
            return
        pos, dur = self._fp_progress()
        state = fv.primary(pos, dur, fv.minutes_of(row, (self._fp_head or {}).get("info"),
                                                   dur_secs=dur))
        self._film_play(row, state.pos if state.resume else 0.0)

    @from_pane
    def _film_play(self, row: dict, at: float | None = None) -> bool:
        """A film in the docked player, the middle pane left as it is,
        through the window's own path. *at*: where it starts, without the
        resume question (0: from the start; None: the question, as the
        list's double-click asks it). False when nothing could start."""
        if not isinstance(row, dict):
            return False
        sid, ext = row.get("stream_id"), row.get("container_extension")
        key = self._item_key(row)
        p = getattr(self, "player", None)
        if (key is not None and str(key) == str(getattr(self, "_playing_key", None))
                and getattr(self, "_playing_group", None) == "vod"
                and p is not None and getattr(p, "current_url", None)):
            return True                 # it plays already: nothing to start over
        if sid is not None and self._links_later(
                lambda: self.client.vod_url(sid, ext), partial(self._film_play, row, at)):
            return True
        url = (self.client.vod_url(sid, ext) if sid is not None else "") or row.get("_url") or ""
        if not url:
            self._set_status(tr("fp_no_address"), error=True)
            return False
        if at is not None:
            self._sp_start_once = (key, float(at))
        try:
            self._start_playback(url, row.get("name") or row.get("title") or "dopeIPTV",
                                 row.get("stream_icon") or row.get("cover"), key, "movie",
                                 record=True, item=row)
        finally:
            self._sp_start_once = None
        return True

    # -- the actions -----------------------------------------------------------------

    def _fp_toggle_fav(self, on: bool) -> None:
        row = self._fp_row
        if row is not None and row.get("stream_id") is not None:
            self._toggle_media_fav(row, "movie", bool(on))
        self._fp_refresh()
        self._pf_refresh_soon()

    def _fp_toggle_later(self, on: bool) -> None:
        row = self._fp_row
        if row is None or row.get("stream_id") is None:
            return
        if on:
            self._add_watchlist(row, "vod", False)
        else:
            self._remove_watchlist(row, "vod", False)
        self._fp_refresh()
        self._pf_refresh_soon()

    def _fp_more_menu(self, pos: QPoint) -> None:
        """The header's ⋯: the film's own menu, as the list's right click
        has it."""
        row = self._fp_row
        if row is None:
            return
        with self._from_pane():             # what it plays leaves the page up
            self._context_menu(None, item=row, at=pos)
        if self._film_page is not None and self._fp_row is row:
            if self._fp_head is not None:
                self._fp_head.update(asked=False)
            self._fp_ask_tmdb()             # a TMDB match picked there
            self._fp_fill()
        self._pf_refresh_soon()

    def _fp_cast_clicked(self, name: str, person_id) -> None:
        if getattr(self, "tmdb", None) is not None and name:
            self._on_cast_clicked(name, person_id)

    # -- leaving it ------------------------------------------------------------------

    def _close_film_page(self) -> None:
        """What the page was opened over back in the middle pane (the page
        kept, hidden)."""
        if self._fp_timer is not None:
            self._fp_timer.stop()
        if self._film_page_showing():
            self._mid_stack.setCurrentIndex(0)

    def _leave_film_page(self) -> None:
        """Back where you were: the Movies place exactly as it was, its
        entry lit."""
        if not self._film_page_showing():
            return
        self._close_film_page()
        if self._films_place_showing():
            self._pf_entered()
        else:
            self._check_nav(self._nav_key_for_mode(self.mode))
        self._pf_back_from_page()

    def _fp_category_clicked(self, _item=None) -> None:
        """A category picked in the sidebar: its films, in the page's place."""
        if self._film_page_showing():
            self._close_film_page()
            self._check_nav(self._nav_key_for_mode(self.mode))

    def _fp_source_changed(self) -> None:
        """Another source (or profile): this film is not its own."""
        self._fp_gen += 1
        if self._film_page_showing():
            self._close_film_page()
        self._fp_row = None
        self._fp_head = None
        if self._film_page is not None:
            self._film_page.reset()

    def _fp_restyle(self) -> None:
        page = self._film_page
        if page is None:
            return
        page.restyle()
        page.retranslate()
        if self._fp_row is not None:
            if self._fp_head is not None:
                self._fp_head["cast_set"] = False
            self._fp_fill()
