"""Home: a full-window start page - a featured strip at the top and
horizontal shelves (continue watching, favorites now, recently viewed,
popular, recently added) in the style of modern TV apps. Lives in the central stack ON TOP of the classic
three-column view: showing it swaps the whole window content, clicking
anything (or Esc) drops back to the classic view and acts there.

Data comes exclusively from services the window already owns (resume,
history, favorites, EPG, the provider client and the cover-art pipeline),
so this module is pure presentation + wiring.
"""

from __future__ import annotations

import heapq
import json
import os
import time

from PyQt6.QtCore import QStandardPaths, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core.log import log
from ..core.workers import run_async
from ..i18n import current_language, tr
from ..services import popular
from .home_widgets import (  # noqa: F401 - _Card/_Shelf are this page's API
    CHAN_H, CHAN_W, POSTER_H, POSTER_W, WIDE_H, WIDE_W, _Card, _Hero, _Shelf,
    chevron_icon, rating_text, split_year,
)
from .theme import P, rounded

def _rgba(key: str, a: float) -> str:
    c = QColor(P[key])
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"


# How many titles the featured strip turns through.
FEATURED = 5


class HomePage(QWidget):
    """The scrollable Home canvas. refresh() rebuilds everything from the
    window's stores; provider lists (movies/series) load async with a
    10-minute cache kept on the window."""

    MEDIA_CACHE_SECS = 600

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self._gen = 0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Opaque viewport so a vertical scroll fully repaints under the moved
        # content instead of smearing transparent labels (the garbled-title
        # ghosting on macOS).
        self._scroll.viewport().setAutoFillBackground(True)
        self._scroll.viewport().setStyleSheet(f"background:{P['bg']};")
        outer.addWidget(self._scroll)
        self._canvas = QWidget()
        # Own dark background, ID-scoped so it doesn't cascade onto children
        # (the app theme normally covers this; explicit = robust everywhere).
        self._canvas.setObjectName("HomeCanvas")
        self._canvas.setStyleSheet(
            f"#HomeCanvas {{ background: {P['bg']}; }}")
        self._v = QVBoxLayout(self._canvas)
        # The featured strip runs edge to edge; the shelves sit in _body.
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(0)
        self._body = QVBoxLayout()
        self._hero: _Hero | None = None
        self._scroll.setWidget(self._canvas)

    # -- building --------------------------------------------------------------

    def _clear(self) -> None:
        """Tear down the whole page, including widgets inside NESTED layouts.

        The Movies/Series and Channels shelves live in sub-layouts
        (_movies_box/_channels_box added via addLayout); the old flat loop
        only handled direct widgets, so those shelves were never deleted -
        they stayed alive as canvas children at their old geometry, painting
        BEHIND every rebuilt page ("stuff behind Featured"), and piled up
        with each refresh. Recurse into layouts, and hide each widget
        immediately: a deleteLater'd widget remains visible until the
        deferred delete runs, which double-exposed the old page under the
        new one in the meantime."""
        def purge(lay) -> None:
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                sub = it.layout()
                if w is not None:
                    w.hide()
                    w.deleteLater()
                elif sub is not None:
                    purge(sub)
        purge(self._v)

    # One connected, pill-shaped nav group: a rounded container holds the
    # buttons edge-to-edge, and each button gets a soft rounded highlight on
    # hover (no hard borders) - so the row reads as a single smooth segment.
    _SEG_QSS = (
        "#HomeNavGroup { background: %(group)s; border: 1px solid %(edge)s;"
        " border-radius: 19px; }"
        "#HomeNavBtn { background: transparent; color: %(text)s; border: none;"
        " border-radius: 15px; padding: 7px 18px; font-size: 13px;"
        " font-weight: 600; }"
        "#HomeNavBtn:hover { background: %(hover)s; }"
    )

    def _top_bar(self) -> QWidget:
        """Quick-nav segment (like the reference apps' top bar) + a close X.
        Text-only labels sitting inside one rounded pill, each with a rounded
        per-item hover highlight."""
        ac = QColor(P["accent"])
        qss = self._SEG_QSS % {
            "pane": QColor(P["pane"]).lighter(112).name(), "text": P["text"],
            "border": QColor(P["pane"]).lighter(135).name(),
            # A translucent accent so the hovered item glows in the accent
            # colour while the label stays readable on top.
            "hover": f"rgba({ac.red()}, {ac.green()}, {ac.blue()}, 0.28)",
            # Over the featured artwork: a glassy pill, readable on any of it.
            "group": _rgba("bg", 0.55), "edge": _rgba("text", 0.10)}
        qss = rounded(qss)
        bar = QWidget(objectName="HomeTopBar")
        # Over the featured artwork: no strip of its own behind it.
        bar.setStyleSheet("#HomeTopBar { background: transparent; }")
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(10)
        w = self.window

        # Top-LEFT: a round chip with a back chevron that returns to the
        # classic three-column view (replaces the old close X in the right
        # corner - leaving Home is navigation, and navigation-back lives on
        # the left).
        back = QPushButton()
        back.setIcon(chevron_icon(16, P["text"]))
        back.setFixedSize(36, 34)
        back.setToolTip(tr("common_close"))
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(rounded(
            "QPushButton { background: %s; border:1px solid %s;"
            " border-radius: 17px; }"
            "QPushButton:hover { border-color: %s; }" % (
                _rgba("bg", 0.55), _rgba("text", 0.12), P["accent"])))
        back.clicked.connect(w._leave_home)
        h.addWidget(back)
        h.addStretch(1)

        group = QFrame(objectName="HomeNavGroup")
        group.setStyleSheet(qss)
        g = QHBoxLayout(group)
        g.setContentsMargins(5, 4, 5, 4)
        g.setSpacing(2)

        def pill(text, fn):
            b = QPushButton(text, objectName="HomeNavBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            g.addWidget(b)

        pill(tr("nav_tv"),
             lambda: self._leave_to(lambda: w.switch_mode("live")))
        pill(tr("btn_epg_guide"), self._open_guide)
        pill(tr("nav_movies"),
             lambda: self._leave_to(lambda: w.switch_mode("vod")))
        pill(tr("nav_series"),
             lambda: self._leave_to(lambda: w.switch_mode("series")))
        pill(tr("btn_settings"), w.open_settings)
        h.addWidget(group)
        h.addStretch(1)
        # Symmetry spacer where the close X used to sit, so the nav pill
        # stays visually centred with the back chip on the left.
        h.addSpacing(36)
        return bar

    def refresh(self) -> None:
        self._gen += 1
        self._clear()
        w = self.window
        s = w.settings

        def on(key):
            return s.value(key, "true") == "true"

        self._hero = _Hero(self._top_bar(), w.poster_art, self._media_art)
        self._hero.play.connect(self._hero_play)
        self._hero.info.connect(self._show_info)
        self._show_hero = on("home_sh_hero")
        self._v.addWidget(self._hero)
        self._body = QVBoxLayout()
        self._body.setContentsMargins(40, 6, 40, 32)
        self._body.setSpacing(30)
        self._v.addLayout(self._body)
        body = self._body

        # One note about donating, once the app has been used for real
        # (mw_donate decides when); a card among the shelves, not a popup.
        note = w._donate_card() if hasattr(w, "_donate_card") else None
        if note is not None:
            body.addWidget(note)

        if on("home_sh_resume"):
            rows = []
            try:
                rows = w.resume.continue_watching()[:14]
            except Exception:
                pass
            if rows:
                shelf = _Shelf(tr("home_resume"))
                for it in rows:
                    left = self._minutes_left(it)
                    card = _Card(WIDE_W, WIDE_H,
                                 split_year(it.get("name") or it.get("title")
                                            or "?")[0],
                                 tr("home_min_left", n=left) if left else "",
                                 progress=int(it.get("_progress_pct") or 0),
                                 style="wide")
                    card.clicked.connect(
                        lambda it=it: self._play_media(it))
                    # Ask the cover pipeline with the row's REAL kind: an
                    # episode requested as "vod" TMDB-searches its mangled
                    # name as a movie - an async wrong/dead candidate that
                    # raced the provider art and left the card blank at
                    # random. As "episode" it resolves the series' poster
                    # (via _series_title), the same stable art the episode
                    # list shows.
                    k = ("episode" if it.get("_kind") == "episode" else "vod")
                    self._set_art(card, self._media_art(it, k), w.poster_art)
                    shelf.add(card)
                shelf.finish(WIDE_H)
                body.addWidget(shelf)

        if on("home_sh_new_eps"):
            rows = []
            try:
                rows = w.followed.with_new()[:20]
            except Exception:
                pass
            if rows:
                from .mw_follow import episode_label
                shelf = _Shelf(tr("home_new_episodes"))
                for row in rows:
                    snap, new = row["snap"], row["new"]
                    sub = (episode_label(new[0]) if len(new) == 1
                           else tr("home_new_count", n=len(new)))
                    card = _Card(POSTER_W, POSTER_H,
                                 snap.get("name") or snap.get("title") or "?",
                                 sub, badge=tr("home_badge_new"))
                    card.clicked.connect(
                        lambda snap=snap: self._open_series(snap))
                    card.right_clicked.connect(
                        lambda snap=snap: self._new_episodes_menu(snap))
                    self._set_art(card, self._media_art(snap, "series"),
                                  w.poster_art)
                    shelf.add(card)
                shelf.finish(POSTER_H)
                body.addWidget(shelf)

        if on("home_sh_fav"):
            chans = self._favorite_channels()[:14]
            if chans:
                shelf = _Shelf(tr("home_fav_now"),
                               more=lambda: self._leave_to(
                                   lambda: w.switch_mode("live")))
                for it in chans:
                    shelf.add(self._channel_card(it, with_now=True))
                shelf.finish(CHAN_H)
                body.addWidget(shelf)

            media = self._favorite_media()[:20]
            if media:
                shelf = _Shelf(tr("home_fav_media"))
                for it in media:
                    kind = "series" if it.get("_kind") == "series" else "vod"
                    card = self._poster_card(it, kind)
                    shelf.add(card)
                shelf.finish(POSTER_H)
                body.addWidget(shelf)

        if on("home_sh_history"):
            rows = list(getattr(w.history, "entries", []))
            # Keep the two shelves in sync: a partly-watched title already
            # sits on Continue watching, repeating it under Recently viewed
            # is pure redundancy. Only filtered while that shelf is shown -
            # with it off, Recently viewed shows everything again. Keys are
            # str()-compared (int/str id mix) and kinds mapped (resume says
            # "vod", history says "movie").
            if on("home_sh_resume"):
                skip = set()
                try:
                    for r in w.resume.continue_watching():
                        k = ("movie" if r.get("_kind") == "vod"
                             else r.get("_kind"))
                        skip.add((k, str(w._item_key(r))))
                except Exception:
                    pass
                rows = [e for e in rows
                        if (e.get("_kind"), str(e.get("_key"))) not in skip]
            rows = rows[:14]
            if rows:
                shelf = _Shelf(tr("home_recent"))
                for it in rows:
                    live = it.get("_kind") == "live"
                    if live:
                        # A channel logo, whole, on a tile.
                        card = _Card(CHAN_W, CHAN_H, it.get("name") or "?",
                                     style="tile")
                        self._set_art(card, it.get("stream_icon"), w.logos,
                                      contain=True)
                    else:
                        name, year = split_year(it.get("name") or "?")
                        card = _Card(POSTER_W, POSTER_H, name, year)
                        # A movie/episode: resolve a real poster through the
                        # cover pipeline (the raw history icon is often a tiny
                        # provider thumb, or missing).
                        k = "episode" if it.get("_kind") == "episode" else "vod"
                        self._set_art(card, self._media_art(it, k),
                                      w.poster_art)
                    card.clicked.connect(
                        lambda it=it: self._play_history(it))
                    shelf.add(card)
                shelf.finish(POSTER_H)
                body.addWidget(shelf)

        if on("home_sh_watchlist"):
            wl = getattr(w, "watchlist", None)
            rows = []
            if wl is not None:
                # Only rows this provider can act on: a movie needs its
                # stream_id to play, a show its series_id to drill into.
                # Trakt-only snapshots without them would make dead cards.
                for it in list(getattr(wl, "movies", [])):
                    if it.get("stream_id") is not None:
                        r = dict(it)
                        r["_kind"] = "movie"
                        rows.append(r)
                for it in list(getattr(wl, "shows", [])):
                    if it.get("series_id") is not None:
                        r = dict(it)
                        r["_kind"] = "series"
                        rows.append(r)
            if rows:
                shelf = _Shelf(tr("nav_watchlist"))
                for it in rows[:20]:
                    # A show drills into its episode list (the pending-drill
                    # dance), like a favorite show.
                    shelf.add(self._poster_card(
                        it, "series" if it["_kind"] == "series" else "vod"))
                shelf.finish(POSTER_H)
                body.addWidget(shelf)

        self._movies_box = QVBoxLayout()
        self._movies_box.setSpacing(30)
        body.addLayout(self._movies_box)
        # Channels load on their own (slower) pass; give them a fixed slot after
        # the movie/series shelves so they always land in the same place no
        # matter which pass finishes first.
        self._channels_box = QVBoxLayout()
        body.addLayout(self._channels_box)
        self._v.addStretch(1)
        self._load_media()

    # -- cards ------------------------------------------------------------------

    @staticmethod
    def _minutes_left(it: dict) -> int:
        try:
            pos = float(it.get("_resume_pos") or 0)
            dur = float(it.get("_resume_dur") or 0)
        except (TypeError, ValueError):
            return 0
        return max(1, round((dur - pos) / 60)) if dur > pos > 0 else 0

    def _poster_card(self, it: dict, kind: str, rank: int | None = None
                     ) -> _Card:
        """A movie or a series on a poster, its year and rating under the
        title: a movie plays, a series opens its episode list."""
        name, year = split_year(it.get("name") or it.get("title") or "?")
        sub = "  ·  ".join(x for x in (year, rating_text(it)) if x)
        card = _Card(POSTER_W, POSTER_H, name, sub, rank=rank)
        if kind == "series":
            card.clicked.connect(lambda it=it: self._open_series(it))
        else:
            card.clicked.connect(lambda it=it: self._play_media(it))
        self._set_art(card, self._media_art(it, kind), self.window.poster_art)
        return card

    def _channel_card(self, it: dict, with_now: bool = False) -> _Card:
        """A channel's tile; with what is on now under it - and LIVE, and
        how far it has got, on it - when the guide knows."""
        w = self.window
        now_t, live = "", None
        if with_now:
            try:
                prog = w.xmltv.current_programme(it) or {}
            except Exception:
                prog = {}
            now_t = prog.get("title") or ""
            try:
                start = float(prog.get("start_timestamp") or 0)
                stop = float(prog.get("stop_timestamp") or 0)
            except (TypeError, ValueError):
                start = stop = 0.0
            if stop > start > 0:
                live = (start, stop)
                now_t = (f"{time.strftime('%H:%M', time.localtime(start))}"
                         f"–{time.strftime('%H:%M', time.localtime(stop))}"
                         f"  {now_t}")
        card = _Card(CHAN_W, CHAN_H, it.get("name") or "?", now_t,
                     style="tile", live=live)
        card.clicked.connect(lambda it=it: self._play_channel(it))
        card.right_clicked.connect(lambda it=it: self._channel_menu(it))
        self._set_art(card, it.get("stream_icon"), w.logos, contain=True)
        return card

    # -- data ------------------------------------------------------------------

    def _favorite_channels(self) -> list[dict]:
        w = self.window
        locked = (set() if w.parental.session_unlocked
                  else set(w.favs.locked_groups()))
        seen, out = set(), []
        for group, items in getattr(w.favs, "groups", {}).items():
            if group in locked:
                continue
            for it in items:
                sid = it.get("stream_id")
                if sid is not None and sid not in seen:
                    seen.add(sid)
                    out.append(it)
        return out

    def _favorite_media(self) -> list[dict]:
        """Favourited movies and series, tagged with the kind so a card can
        play a movie or drill into a series. Channel favourites live in their
        own 'now playing' shelf; this is the poster shelf for VOD content, so
        a user whose favourites are all films/shows still sees them on Home."""
        w = self.window
        out: list[dict] = []
        seen: set = set()
        for store, kind, id_key in (
                (getattr(w, "movie_favs", None), "movie", "stream_id"),
                (getattr(w, "series_favs", None), "series", "series_id")):
            if store is None:
                continue
            for items in getattr(store, "groups", {}).values():
                for it in items:
                    ident = it.get(id_key)
                    tag = (kind, ident)
                    if ident is None or tag in seen:
                        continue
                    seen.add(tag)
                    row = dict(it)
                    row["_kind"] = kind
                    out.append(row)
        return out

    @staticmethod
    def _is_movie(it: dict) -> bool:
        """True for a real VOD movie row - has a stream id and a type that
        isn't a live/radio channel. Filters channels a provider may have mixed
        into the VOD list out of the Movies shelf."""
        if it.get("stream_id") is None:
            return False
        st = str(it.get("stream_type") or "").lower()
        return st not in ("live", "radio", "created_live")

    @staticmethod
    def _num(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    def _load_media(self) -> None:
        # Two independent passes so the hero/poster shelves don't wait on the
        # channel list. The Movies/Series lists are quick; live_streams(None)
        # returns the provider's *entire* channel lineup (often thousands),
        # which is slow - fetching it in the same call was what made the top
        # "Featured" row take a long time to appear.
        self._load_posters()
        self._load_channels()

    def _load_posters(self) -> None:
        w, gen = self.window, self._gen
        chart = self._trending()
        stamp = chart.get("t") if chart else None
        cache = getattr(w, "_home_poster_cache", None)
        # A new chart makes the cached popular shelves out of date.
        if (cache and time.time() - cache[0] < self.MEDIA_CACHE_SECS
                and cache[5] == stamp):
            self._fill_posters(*cache[1:5])
            return
        # Cold start: paint last session's posters straight from disk so the
        # Featured row appears instantly, then refresh from the provider in the
        # background and swap them in. vod_streams(None) returns the whole movie
        # catalogue, which is inherently slow, so this is what makes Home feel
        # fast on launch.
        disk = self._read_disk_posters()
        if disk:
            self._fill_posters(*disk)
        client = w.client
        resolver = getattr(getattr(w, "cover", None), "resolver", None)

        def known(kind: str):
            if resolver is None:
                return None
            return lambda name: resolver.tmdb_id_for(name, kind)

        keep_vod = w._catalog_filter("vod")
        keep_ser = w._catalog_filter("series")
        num = self._num
        is_movie = self._is_movie

        def work():
            vod = list(client.vod_streams(None) or [])
            ser = list(client.series_list(None) or [])
            # The chart is matched against the whole catalogue here: tens
            # of thousands of names are too many for the GUI thread.
            gvod: list = []
            gser: list = []
            if chart:
                gvod = popular.match(chart["vod"], vod, known("vod"))
                gser = popular.match(chart["series"], ser, known("series"))
            # The newest titles, picked here for the same reason: filtering
            # and sorting the whole catalogue in done() held up the window
            # and the player for almost half a second. Each shelf keeps to
            # its own content type - some providers dump live channels into
            # the VOD "all" list - and to ~18 cards, plenty for a row and
            # few widgets for a smooth page scroll.
            vod = heapq.nlargest(
                18, (i for i in vod if is_movie(i) and keep_vod(i)),
                key=lambda i: num(i.get("added")))
            ser = heapq.nlargest(
                18, (i for i in ser
                     if i.get("series_id") is not None and keep_ser(i)),
                key=lambda i: num(i.get("last_modified")))
            return vod, ser, gvod, gser

        def done(res):
            vod, ser, gvod, gser = res
            pvod = self._pick_popular(gvod, "vod")
            pser = self._pick_popular(gser, "series")
            w._home_poster_cache = (time.time(), vod, ser, pvod, pser, stamp)
            self._write_disk_posters(vod, ser, pvod, pser)
            if gen != self._gen:
                return
            try:
                # ALWAYS rebuild the whole page from the just-set memory cache
                # (refresh() hits it synchronously - no second fetch), never
                # poke shelves into the already-painted page: the incremental
                # path revealed the hidden hero, shifting every shelf below it
                # mid-paint - the other half of "stuff behind Featured".
                self.refresh()
            except RuntimeError:
                pass

        run_async(w.pool, work, done, lambda _e: None)

    # -- instant cold-start cache (persisted to QSettings, per playlist) -------

    def _disk_key(self) -> str:
        pid = getattr(getattr(self.window, "playlist_store", None),
                      "active_id", "") or "default"
        return f"home_pcache_{pid}"

    def _read_disk_posters(self):
        try:
            raw = self.window.settings.value(self._disk_key(), "") or ""
            d = json.loads(raw) if raw else None
        except Exception:
            d = None
        if not isinstance(d, dict):
            return None
        try:
            # Stale after a week - better to wait for the network than show
            # week-old "recently added".
            if time.time() - float(d.get("t", 0)) > 7 * 24 * 3600:
                return None
        except (TypeError, ValueError):
            return None
        rows = tuple(d.get(k) or [] for k in ("vod", "ser", "pvod", "pser"))
        return rows if any(rows) else None

    def _write_disk_posters(self, vod: list, ser: list, pvod: list,
                            pser: list) -> None:
        try:
            self.window.settings.setValue(
                self._disk_key(),
                json.dumps({"t": time.time(), "vod": vod, "ser": ser,
                            "pvod": pvod, "pser": pser}))
        except Exception:
            pass

    # -- popular right now (TMDB's chart of the week, what the provider has) --

    POPULAR_MAX = 18

    def _trending(self) -> dict | None:
        """This week's TMDB chart for the popular shelves, from memory or
        disk - fetched again in the background once it is a day old or in
        another language. None while there is none, or no shelf wants it."""
        w = self.window
        s = w.settings
        if (s.value("home_sh_pop_movies", "true") != "true"
                and s.value("home_sh_pop_series", "true") != "true"):
            return None
        if getattr(w, "_popular_chart", None) is None:
            w._popular_chart = popular.load(self._chart_path()) or {}
        chart = w._popular_chart
        now, lang = time.time(), current_language()
        # The chart comes from TMDB, through the key the covers use.
        resolver = getattr(getattr(w, "cover", None), "resolver", None)
        if resolver is None and not getattr(w, "_popular_nokey_said", False):
            # Silent otherwise: the shelves just stay away once the chart on
            # disk is a week old. A build from source has no built-in key.
            w._popular_nokey_said = True
            log.info("popular: no TMDB key, so no chart for the popular "
                     "shelves (a source checkout has no built-in key: set "
                     "DOPEIPTV_TMDB_KEY, or a key of your own in Settings)")
        if (resolver is not None and not popular.is_fresh(chart, lang, now)
                and not getattr(w, "_popular_fetching", False)
                and now - getattr(w, "_popular_failed", 0.0)
                > popular.RETRY_AFTER_S):
            self._fetch_chart(resolver.client, lang)
        return chart if popular.usable(chart, now) else None

    def _fetch_chart(self, client, lang: str) -> None:
        w = self.window
        w._popular_fetching = True
        path = self._chart_path()

        def work():
            chart = popular.fetch(client, lang)
            if path:
                try:
                    popular.save(path, chart)
                except OSError as e:
                    log.info("popular: could not keep the chart: %s", e)
            return chart

        def done(chart):
            w._popular_fetching = False
            w._popular_chart = chart
            log.info("popular: TMDB chart of %d movies and %d series",
                     len(chart["vod"]), len(chart["series"]))
            if w._home_showing():
                w._home_page.refresh()

        def failed(msg):
            w._popular_fetching = False
            w._popular_failed = time.time()
            log.info("popular: no TMDB chart: %s", msg)

        run_async(w.pool, work, done, failed)

    @staticmethod
    def _chart_path() -> str:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation)
        return os.path.join(base, "tmdb_trending.json") if base else ""

    def _pick_popular(self, groups: list, kind: str) -> list[dict]:
        """For each title on the chart, the first copy the user may see: not
        hidden, not locked away, and on the movie shelf a real movie."""
        flat = [row for rows in groups for row in rows]
        seen = {id(r) for r in self.window._visible_catalog(flat, kind)}

        def ok(row: dict) -> bool:
            if id(row) not in seen:
                return False
            if kind == "vod":
                return self._is_movie(row)
            return row.get("series_id") is not None

        return popular.pick(groups, ok, self.POPULAR_MAX)

    def _load_channels(self) -> None:
        w, gen = self.window, self._gen
        if w.settings.value("home_sh_channels", "true") != "true":
            return   # shelf off - don't pull the whole channel list at all
        cache = getattr(w, "_home_chan_cache", None)
        if cache and time.time() - cache[0] < self.MEDIA_CACHE_SECS:
            self._fill_channels(cache[1])
            return
        client = w.client
        keep = w._catalog_filter("live")
        num = self._num

        def work():
            # Picked here, not in done(): see _load_posters.
            return heapq.nlargest(
                18, (i for i in client.live_streams(None) or []
                     if i.get("stream_id") is not None and keep(i)),
                key=lambda i: num(i.get("added")))

        def done(chan):
            w._home_chan_cache = (time.time(), chan)
            if gen == self._gen:
                try:
                    self._fill_channels(chan)
                except RuntimeError:
                    pass

        run_async(w.pool, work, done, lambda _e: None)

    def _fill_posters(self, vod: list, ser: list, pvod: list | None = None,
                      pser: list | None = None) -> None:
        w = self.window
        s = w.settings
        # Again here: the memory and disk copies may predate a lock or a hide.
        vod = w._visible_catalog(vod, "vod")
        ser = w._visible_catalog(ser, "series")
        pvod = w._visible_catalog(pvod or [], "vod")
        pser = w._visible_catalog(pser or [], "series")
        if self._hero is not None and getattr(self, "_show_hero", True):
            self._hero.set_items(self._featured(vod, ser, pvod, pser))

        to_vod = lambda: self._leave_to(lambda: w.switch_mode("vod"))  # noqa: E731
        to_ser = lambda: self._leave_to(lambda: w.switch_mode("series"))  # noqa: E731
        for key, title, rows, kind, ranked, more in (
                ("home_sh_pop_movies", "home_pop_movies", pvod, "vod", True,
                 to_vod),
                ("home_sh_pop_series", "home_pop_series", pser, "series", True,
                 to_ser),
                ("home_sh_movies", "home_new_movies", vod, "vod", False,
                 to_vod),
                ("home_sh_series", "home_new_series", ser, "series", False,
                 to_ser)):
            if s.value(key, "true") == "true" and rows:
                self._poster_shelf(tr(title), rows, kind, ranked, more)
        if vod or ser or pvod or pser:
            # Adding the movie/series shelves grows the page; on macOS a layout
            # reflow can leave the shelves below painted behind their old
            # position. Force a clean repaint once the layout has settled.
            self._repaint_after_reflow()

    @staticmethod
    def _featured(vod: list, ser: list, pvod: list, pser: list) -> list:
        """What the strip at the top turns through: this week's most popular
        the provider has, else what it added last."""
        out = [(it, "vod", f"#{n + 1}  ·  {tr('home_pop_movies')}")
               for n, it in enumerate(pvod[:FEATURED])]
        out += [(it, "series", f"#{n + 1}  ·  {tr('home_pop_series')}")
                for n, it in enumerate(pser[:max(0, FEATURED - len(out))])]
        if not out:
            out = ([(it, "vod", tr("home_new_movies")) for it in vod[:FEATURED]]
                   or [(it, "series", tr("home_new_series"))
                       for it in ser[:FEATURED]])
        return out

    def _poster_shelf(self, title: str, rows: list, kind: str,
                      ranked: bool = False, more=None) -> None:
        shelf = _Shelf(title, more=more)
        for n, it in enumerate(rows):
            shelf.add(self._poster_card(
                it, kind, rank=n + 1 if ranked and n < 10 else None))
        shelf.finish(POSTER_H)
        self._movies_box.addWidget(shelf)

    def _repaint_after_reflow(self) -> None:
        """Force a clean repaint after a layout change that moved shelves.

        Qt on macOS doesn't always repaint widgets that a layout reflow shifts,
        so a grown shelf row can leave the shelves below it ghosted behind it -
        and filling/showing shelves while the page is hidden (returning to Home
        while a stream plays) settles the geometry off-screen where a plain
        update() never lands. Settle the layout, then repaint the canvas and
        viewport *synchronously* so any stale frame is overwritten with the
        page background. Runs on singleShot(0) so the layout is final first."""
        def _rp() -> None:
            try:
                self._v.activate()                    # settle geometry now
                self._canvas.repaint()                # synchronous full clear
                self._scroll.viewport().repaint()
            except RuntimeError:
                pass
        QTimer.singleShot(0, _rp)

    def showEvent(self, e) -> None:
        # Becoming visible after the page was built or filled while hidden
        # (e.g. re-opening Home while something plays, or an async poster fetch
        # that landed after we'd left Home) can leave shelves ghosted behind
        # the hero on macOS. Force a clean repaint every time Home is shown.
        super().showEvent(e)
        self._repaint_after_reflow()

    def _fill_channels(self, chan: list) -> None:
        w = self.window
        chan = w._visible_catalog(chan, "live")
        if w.settings.value("home_sh_channels", "true") != "true" or not chan:
            return
        shelf = _Shelf(tr("home_new_channels"),
                       more=lambda: self._leave_to(
                           lambda: w.switch_mode("live")))
        for it in chan:
            shelf.add(self._channel_card(it))
        shelf.finish(CHAN_H)
        self._channels_box.addWidget(shelf)

    def _media_art(self, it: dict, kind: str = "vod") -> str | None:
        # Episodes rarely carry their own poster: borrow the series'
        # artwork from the stored context so an episode card still shows a
        # poster instead of a bare initial.
        ctx = it.get("_series_ctx") or {}
        raw = (it.get("cover") or it.get("stream_icon")
               or ctx.get("cover") or ctx.get("stream_icon"))
        try:
            return self.window.cover.cover_url(it, kind) or raw
        except Exception:
            return raw

    def _set_art(self, card: _Card, url, loader, contain: bool = False) -> None:
        if not url or loader is None:
            return
        gen = self._gen

        def cb(pm, card=card, gen=gen, contain=contain):
            if gen != self._gen or pm is None or pm.isNull():
                return
            try:
                card.set_pixmap(pm, contain=contain)
            except RuntimeError:
                pass   # card torn down by a rebuild

        loader.get(url, cb)

    # -- actions (always leave Home first, then act in the classic UI) --------

    def _leave_to(self, fn) -> None:
        self.window._leave_home()
        fn()

    def _open_guide(self) -> None:
        self.window._leave_home()
        # Global scope: from Home the guide must not inherit whatever
        # category the hidden classic view had auto-selected.
        self.window._open_epg_guide(global_scope=True)

    def _play_channel(self, it: dict) -> None:
        # Tune the channel, then land the classic list under TV *in this
        # channel's own category* and select it (works whether or not we were
        # already in Live mode). Order matters:
        #  1. play - sets _playing_key so the list's pending-jump can select it;
        #  2. reveal - switches mode to live *synchronously* (list fills async);
        #  3. detail - with mode now live, the panel classifies the item as a
        #     channel and fetches its EPG (before the reorder it ran under the
        #     old mode and could fetch movie info / the old row's guide);
        #  4. stop the preview timer - _show_detail arms it, and 350 ms later
        #     it would re-tune to whatever OLD row the list still had selected.
        w = self.window
        w._leave_home()
        try:
            w.play_live_channel(it)
        except Exception:
            pass
        try:
            w._reveal_channel_in_list(it)
        except Exception:
            pass
        try:
            w._current_key = w._item_key(it)
            w._show_detail(it)   # panel follows the channel, not the last row
        except Exception:
            pass
        try:
            w._preview_timer.stop()
        except Exception:
            pass

    def _channel_menu(self, it: dict) -> None:
        """Right-click on a Home channel tile: play, set a reminder, or record
        - the same options as the classic list and the 'not started' prompt,
        without leaving Home."""
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QMenu
        if it.get("stream_id") is None:
            return
        w = self.window
        m = QMenu(self)
        m.addAction(tr("common_watch"), lambda: self._play_channel(it))
        m.addSeparator()
        m.addAction(tr("upcoming_remind"), lambda: w._remind_upcoming(it))
        m.addAction(tr("upcoming_record_stop"), lambda: w._record_now(it, None))
        m.addAction(tr("upcoming_schedule"),
                    lambda: w._schedule_recording(it))
        m.exec(QCursor.pos())

    def _new_episodes_menu(self, snap: dict) -> None:
        """Right-click on a New episodes card: open it, dismiss the news
        without opening it, or stop following the series."""
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QMenu
        w = self.window
        sid = snap.get("series_id")
        m = QMenu(self)
        m.addAction(tr("common_open"), lambda: self._open_series(snap))
        m.addSeparator()
        m.addAction(tr("common_dismiss"), lambda: w._follow_mark_seen(sid))
        m.addAction(tr("ctx_unfollow_series"),
                    lambda: w._unfollow_series(snap))
        m.exec(QCursor.pos())

    def _hero_play(self, it: dict, kind: str) -> None:
        if kind == "series":
            self._open_series(it)
        else:
            self._play_media(it)

    def _show_info(self, it: dict, kind: str) -> None:
        """More info: the title in its own list and its details, without
        playing anything."""
        if kind == "series":
            self._open_series(it)
            return
        w = self.window
        w._leave_home()
        w._reveal_item_in_list(it, "vod")
        try:
            w._current_key = w._item_key(it)
            w._show_detail(it)
        except Exception:
            pass

    def _play_media(self, it: dict) -> None:
        """Movie or a continue-watching row: land under Movies, then play it.
        Built directly (not via play_item, whose URL/kind logic keys off the
        classic view's mode - a movie clicked from Home would otherwise be
        built as a live URL)."""
        w = self.window
        w._leave_home()
        # A continue-watching episode replays from its stored series context.
        if it.get("_kind") == "episode" and it.get("_series_ctx") is not None:
            # Play first: the continue-episode path builds its URL from the
            # stored context, independent of whatever mode the list is in.
            w.play_item(it)
            # Then land the middle column inside the series' episode list with
            # this episode selected - the same drill + pending-jump dance as
            # the now-playing jump. Just calling switch_mode left the user on
            # "all series": its async category load resets the list when it
            # lands, so the drill must ride the category-load callback.
            ctx = it.get("_series_ctx") or {}
            if ctx.get("series_id") is not None:
                w._pending_jump_key = w._item_key(it)
                QTimer.singleShot(8000, w._clear_pending_jump)
                if w.mode != "series":
                    w._pending_series_drill = ctx
                    w.switch_mode("series")
                else:
                    w._enter_series(ctx)
            elif w.mode != "series":
                w.switch_mode("series")
            return
        sid = it.get("stream_id")
        # A snapshot-derived row (Continue watching seeded by a play from
        # History) carries no provider id, but it does keep the URL it was
        # played from - and that one is known-good. Without this fallback the
        # card bailed out here and clicking it did nothing at all.
        url = (w.client.vod_url(sid, it.get("container_extension"))
               if sid is not None else it.get("_url"))
        if not url:
            return
        # Land the Movies list in THIS movie's own category with it selected,
        # instead of leaving the user in whatever category was last open.
        w._reveal_item_in_list(it, "vod")
        # Populate the detail panel with THIS movie. Home plays straight from a
        # card without selecting a list row, so without this the panel under
        # the player keeps showing whatever was last selected in the classic
        # view (e.g. the TV channel played before). Set _current_key too, so the
        # poster's play/pause overlay recognises this as the playing item and
        # syncs (otherwise it kept showing 'play' while the movie ran).
        try:
            w._current_key = w._item_key(it)
            w._show_detail(it)
        except Exception:
            pass
        w._start_playback(
            url, it.get("name") or it.get("title") or "dopeIPTV",
            it.get("stream_icon") or it.get("cover"),
            w._item_key(it), "movie", item=it)

    def _open_series(self, it: dict) -> None:
        # Land under Series and drill straight into this show's episode list.
        w = self.window
        w._leave_home()
        if w.mode != "series":
            # Switching modes reloads the Series categories asynchronously; when
            # that lands it would reset the middle list to "all series" and undo
            # an immediate _enter_series (the "first click just shows all shows"
            # bug). Hand the drill to the category-load callback instead.
            w._pending_series_drill = it
            w.switch_mode("series")
        else:
            w._enter_series(it)

    def _play_history(self, it: dict) -> None:
        """Replay a history row: land under the right category, then play from
        its stored URL + kind (works regardless of the prior mode)."""
        w = self.window
        w._leave_home()
        url = it.get("_url")
        if not url:
            return
        kind = it.get("_kind") or "live"
        ctx = it.get("_series_ctx") or {}
        if kind == "episode" and ctx.get("series_id") is None:
            # An old entry recorded before the series snapshot existed: no
            # context to resume/drill with, so it can only replay as a movie.
            kind = "movie"
        if kind == "episode":
            # Same landing as a continue-watching card: play as an EPISODE
            # (so the resume prompt finds its saved position) and drill the
            # middle column into the series' episode list with this episode
            # selected - the drill rides the async category load, a plain
            # switch_mode landed on "all series".
            w._pending_jump_key = it.get("_key")
            QTimer.singleShot(8000, w._clear_pending_jump)
            if w.mode != "series":
                w._pending_series_drill = ctx
                w.switch_mode("series")
            else:
                w._enter_series(ctx)
        else:
            target = {"live": "live", "movie": "vod"}.get(kind)
            if target and w.mode != target:
                w.switch_mode(target)
        try:
            w._current_key = it.get("_key")
            w._show_detail(it)   # panel follows this row, not the last selection
        except Exception:
            pass
        w._start_playback(url, it.get("name") or "dopeIPTV",
                          it.get("stream_icon"), it.get("_key"), kind, item=it)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.window._leave_home()
            return
        super().keyPressEvent(e)


class _HomeMixin:
    """MainWindow glue: nav button, central-stack swapping, settings."""

    def _home_enabled(self) -> bool:
        return self.settings.value("home_enabled", "true") == "true"

    def _ensure_home_page(self) -> HomePage:
        page = getattr(self, "_home_page", None)
        if page is None:
            page = self._home_page = HomePage(self)
            self._center_stack.addWidget(page)
        return page

    def _show_home_page(self) -> None:
        if not self._home_enabled():
            return
        # Clear any classic-view loading strip/overlay so it doesn't linger
        # behind (and reappear on) the Home page.
        self._hide_busy()
        page = self._ensure_home_page()
        page.refresh()
        self._center_stack.setCurrentWidget(page)
        page.setFocus()
        for k, b in self.nav_btns.items():
            b.setChecked(k == "home")

    def _leave_home(self) -> None:
        self._center_stack.setCurrentIndex(0)
        if "home" in self.nav_btns:
            self.nav_btns["home"].setChecked(False)
        if self.mode in self.nav_btns:
            self.nav_btns[self.mode].setChecked(True)
        # The middle pane was hidden while Home showed, so its width was stale
        # and the control strip could sit in the wrong compact/full form (icons
        # despite a wide window). Recompute once the classic view has laid out.
        QTimer.singleShot(0, self._update_mid_compact)

    def _home_showing(self) -> bool:
        page = getattr(self, "_home_page", None)
        return page is not None and self._center_stack.currentWidget() is page

    def _apply_home_settings(self) -> None:
        if "home" in self.nav_btns:
            self.nav_btns["home"].setVisible(self._home_enabled())
        if not self._home_enabled() and self._home_showing():
            self._leave_home()
        elif getattr(self, "_home_page", None) is not None:
            # A shelf switched on or off in Settings shows so at once - and
            # a page built but not on screen just now is rebuilt too, or it
            # kept the shelf until it was shown again.
            self._home_page.refresh()

    def _maybe_open_home_at_start(self) -> None:
        if (self._home_enabled()
                and self.settings.value("home_start", "false") == "true"):
            QTimer.singleShot(0, self._show_home_page)
