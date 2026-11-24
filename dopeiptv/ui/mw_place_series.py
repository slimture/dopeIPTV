"""MainWindow mixin: the Series place (ui/series_place.py draws it).

The source's series categories stay in the left column as they were; the
middle pane shows, where the list was, the shared form of every place:
the category's name with its count and order, a filter, the sort and the
grid / list switch (remembered, ``place_series_view``), "Popular right
now" (ui/mw_popular.py: the chart of the week matched to this source's
series, with their ranks - a category's own in a category; hidden when
nothing matches, TMDB is off or Settings > Posters & info says so),
"Continue" (each series under way with its next episode and how far it
got, core/series_continue.py: from the resume points and the series
page's cache - never a row of series_info requests), then the category's
series as poster cards (a followed one with news says "New episode") or
rows of a list.

The list under it is the source of truth and keeps doing all it did: the
categories, hidden and locked ones and the PIN, a kids profile, hidden
and renamed rows, the filter (the field here types into the list's), the
sort (each category its own, recently updated first where the source
says when), the menu of a series (``_list_item_menu``: favourites and
folders, Watch later, Follow, TMDB, marks - for every card selected), the
selection the detail pane follows, the jumps of the middle pane that
follows what plays. The place draws what the list holds after its model
is reset, a moment later, card for card.

The place answers mw_places' hooks: ``_place_series_show`` (the sidebar's
Series entry, ``_show_place("series")``, and Back from a page over it,
``_return_to_place``) and ``_place_series_refresh`` (a profile, a source,
a followed series, a sync). A series opens its page (ui/mw_series_page.py)
over the place; Back shows the place exactly as it was - the grid, its
scroll and the card chosen.
Continue plays its episode through the window's own path, the series as
its context (``_play_series_episode``), from where it got to without the
resume question, as the page's Continue does; the place stays.
"""

from __future__ import annotations

from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import QItemSelectionModel, QPoint, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMenu

from ..core import series_continue as sc
from ..core import series_view as sv
from ..core.log import log
from ..i18n import tr
from .media_cards import item_marks
from .mw_sort import ORDER_WORDS, order_words
from .series_place import SeriesCard, SeriesPlace

#: The grid or the list, remembered ("poster" / "list").
VIEW_KEY = "place_series_view"
#: Most series on "Popular right now" (each with its rank) - out of this
#: many of the source's asked for, so a category still finds its own.
POPULAR_MAX = 10
POPULAR_ASK = 100
#: The detail pane follows the card chosen by the keyboard after this long
#: (arrowing past a card asks nothing for it).
SELECT_SYNC_MS = 350
#: The categories that are lists of their own: no rows over them.
_OWN_LISTS = ("__continue__", "__recent__", "__followed__")
_TITLE_KEYS = {"__continue__": "cat_continue", "__recent__": "cat_recent",
               "__followed__": "cat_following"}


def series_key(it: dict) -> str:
    """A card's key: a series by its id, an episode (Continue watching's
    category) by its own."""
    if it.get("_kind") == "episode" or (it.get("series_id") is None
                                        and it.get("id") is not None):
        return f"e:{it.get('id') or it.get('_key')}"
    return f"s:{it.get('series_id')}"


class _PlaceSeriesMixin:
    """MainWindow mixin: the Series place."""

    _place_series: SeriesPlace | None = None
    _place_series_pending = False
    _place_series_rows_pending = False
    _place_series_popular_watched = False
    _place_series_syncing = False
    _place_series_sel_timer: QTimer | None = None
    _place_series_row_of: dict | None = None
    _place_series_sigs: dict | None = None

    # -- where it is ------------------------------------------------------------------

    def _place_series_active(self) -> bool:
        """The place is where the list is (it may be under another page)."""
        place, stack = self._place_series, getattr(self, "_list_stack", None)
        return (place is not None and stack is not None and not sip.isdeleted(place)
                and stack.currentWidget() is place)

    def _place_series_showing(self) -> bool:
        """...and the middle pane shows it (not a series' page, the Guide,
        Search over it)."""
        return (self._place_series_active()
                and self._mid_stack.currentWidget() is self._list_page)

    def _place_series_show(self) -> bool:
        """The Series place in the middle pane: mw_places' hook for the
        sidebar's Series entry (``_show_place``) and for Back from a page
        over it (``_return_to_place``) - the Guide, Search or a series'
        page give way (a series' page as its Back would: the place as it
        was); the source's series categories in the left column, the cards
        take the keys. True: it shows, or is on its way while the
        categories load."""
        self._close_guide_page()
        self._close_series_page()
        self._close_search_page()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        if self.mode != "series":
            self.switch_mode("series")
        else:
            self._place_series_sync()
        entered = getattr(self, "_place_entered", None)
        if callable(entered):
            entered("series")
        if self._place_series_may_focus():
            self._place_series_take_focus()
        return True

    def _place_series_may_focus(self) -> bool:
        """Whether the cards may take the keys now: not from "Who's
        watching?", the welcome, TV mode, a dialog or a menu."""
        if getattr(self, "_profile_picker", None) is not None or getattr(self, "_tv_on", False):
            return False
        welcome = getattr(self, "_welcome", None)
        if welcome is not None and welcome.isVisible():
            return False
        return (QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None)

    def _place_series_sync(self) -> None:
        """switch_mode: the place where the list is while Series is the
        mode, the list itself otherwise."""
        stack = getattr(self, "_list_stack", None)
        if stack is None:
            return
        if self.mode == "series":
            place = self._ensure_place_series()
            if stack.currentWidget() is not place:
                stack.setCurrentWidget(place)
            self._place_series_rebuild()
        elif stack.currentWidget() is not self._list_body:
            stack.setCurrentWidget(self._list_body)

    def _place_series_take_focus(self) -> bool:
        """The keyboard's focus to the place's cards (Back from a series'
        page); False when the place does not show."""
        if not self._place_series_showing():
            return False
        self._place_series.grid.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _ensure_place_series(self) -> SeriesPlace:
        place = self._place_series
        if place is not None and not sip.isdeleted(place):
            return place
        place = SeriesPlace(self.logos, self._place_series_layout())
        grid = place.grid
        grid.fill = self._place_series_fill
        grid.activated.connect(self._place_series_activate)
        grid.menu.connect(self._place_series_menu)
        grid.current_item_changed.connect(self._place_series_current)
        place.popular.activated.connect(self._place_series_open)
        place.popular.menu.connect(self._place_series_popular_menu)
        # Continue plays: one click only with autoplay on, as in the lists;
        # Popular right now opens a series' page - one click, as a card does.
        place.cont.strip.click_plays = self._autoplay_preview
        place.cont.activated.connect(self._place_series_continue)
        place.cont.menu.connect(self._place_series_continue_menu)
        # Each hidden from its heading (mw_place_look; Popular right now in
        # its own key on Posters & info); compact while Just the list.
        for row, name in ((place.popular, "series_popular"), (place.cont, "series_continue")):
            row.set_hide_key(name)
            row.hide_requested.connect(self._place_row_hide)
        self._place_look_adopt(place)
        place.header.sort_changed.connect(self._place_series_sort)
        place.header.view_changed.connect(self._place_series_view)
        place.filter.setText(self.search.text())
        place.filter.textChanged.connect(self._place_series_filter)
        self.search.textChanged.connect(self._place_series_search_changed)
        self.list_model.modelReset.connect(self._place_series_soon)
        self.list_model.rowsInserted.connect(self._place_series_soon)
        self.list_model.dataChanged.connect(self._place_series_art_changed)
        self.listw.selectionModel().currentChanged.connect(self._place_series_list_moved)
        self._mid_stack.currentChanged.connect(self._place_series_stack_changed)
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(SELECT_SYNC_MS)
        t.timeout.connect(self._place_series_sync_selection)
        self._place_series_sel_timer = t
        self._list_stack.addWidget(place)
        self._place_series = place
        self._place_series_sigs = {}
        return place

    def _place_series_layout(self) -> str:
        """Posters or rows: the person's own - rows while Just the list is
        on, unless the switch picked posters since (mw_place_look)."""
        v = str(self.settings.value(VIEW_KEY, "poster") or "poster")
        v = v if v in ("poster", "list") else "poster"
        view = getattr(self, "_place_view", None)
        if callable(view):
            v = view("series", v)
        return v if v in ("poster", "list") else "poster"

    # -- the cards: what the list holds ---------------------------------------------

    def _place_series_soon(self, *_a) -> None:
        """The list's model was reset: the cards follow a moment later (one
        rebuild for a burst of resets)."""
        if self._place_series is None or self._place_series_pending:
            return
        self._place_series_pending = True
        QTimer.singleShot(0, self._place_series_rebuild)

    def _place_series_rebuild(self) -> None:
        self._place_series_pending = False
        place = self._place_series
        if place is None or sip.isdeleted(place) or not self._place_series_active():
            return
        model = self.list_model
        rows = model.items() if model.kind in ("series", "episode") else []
        name = self._display_name_fn()
        cards: list[SeriesCard] = []
        row_of: dict[int, int] = {}
        for i, it in enumerate(rows):
            if not isinstance(it, dict) or it.get("_header"):
                continue
            if it.get("_kind") == "episode":
                ctx = it.get("_series_ctx") or {}
                title = str(ctx.get("name") or ctx.get("title") or name(it))
            else:
                title = name(it)
            cards.append(SeriesCard(key=series_key(it), title=title, data=it))
            row_of[id(it)] = i
        self._place_series_row_of = row_of
        grid = place.grid
        old = grid.items()
        if len(old) == len(cards) and all(a.key == b.key for a, b in zip(old, cards, strict=True)):
            # The same series (a rename, a poster, the list filtered again to
            # what it was): the cards as they are, the scroll and the choice
            # kept - Back from a series' page finds the grid as it left it.
            for a, b in zip(old, cards, strict=True):
                a.data, a.title, a.line, a.pills = b.data, b.title, "", ()
            grid.refresh_art()
        else:
            grid.set_items(cards)
            want = self._place_series_listed_key()
            if want and grid.row_of(want) >= 0:
                QTimer.singleShot(0, partial(self._place_series_reveal, want))
            else:
                place.scroll_to_top()
        self._place_series_head()
        self._place_series_rows()
        self._place_series_mark()

    def _place_series_listed_key(self) -> str:
        """The key of the list's own chosen row (a jump to what plays, Back
        to a series' row), or ""."""
        idx = self.listw.currentIndex()
        it = self.list_model.item_at(idx.row()) if idx.isValid() else None
        return series_key(it) if isinstance(it, dict) and not it.get("_header") else ""

    def _place_series_reveal(self, key: str) -> None:
        place = self._place_series
        if place is None or sip.isdeleted(place) or not self._place_series_active():
            return
        grid = place.grid
        grid.doItemsLayout()
        lay = place.body.layout()
        if lay is not None:
            lay.activate()
        self._place_series_syncing = True
        try:
            grid.focus_key(key, take_focus=False)
        finally:
            self._place_series_syncing = False

    def _place_series_fill(self, card, full: bool) -> None:
        """A card about to be painted: its picture and line (*full*: worked
        out again - the first time, or posters found since), its pills."""
        it = card.data
        if not isinstance(it, dict):
            return
        if full:
            card.image = self._place_series_art(it)
            card.line = self._place_series_line(it)
            if it.get("_kind") == "episode":
                try:
                    card.progress = max(0.0, min(1.0, float(it.get("_progress_pct") or 0) / 100))
                except (TypeError, ValueError):
                    card.progress = None
        card.pills = self._place_series_pills(it)
        if it.get("_kind") != "episode":
            card.marks = item_marks(self, it, "series")

    def _place_series_art(self, it: dict) -> str:
        """A series' poster address as the list chooses it (TMDB's, else
        the source's), once it is worth fetching: the source's own waits
        for TMDB's answer, so a card does not fetch a picture about to be
        replaced. "" meanwhile (its initials show)."""
        cover = self.cover
        try:
            url = cover.cover_url(it, "series")
        except Exception:
            url = None
        if not url:
            return ""
        loader = self.logos
        try:
            if (url in loader.cache or url in loader.waiting or loader.is_dead(url)
                    or cover.should_fetch(url, it, "series")):
                return str(url)
        except Exception:
            return ""
        return ""

    def _place_series_line(self, it: dict) -> str:
        """The muted line under a card: year · rating (or its genre); an
        episode's "S2 E5 · 18 min left"."""
        if it.get("_kind") == "episode":
            season, number = sc.se_of(it)
            try:
                pos, dur = float(it.get("_resume_pos") or 0), float(it.get("_resume_dur") or 0)
            except (TypeError, ValueError):
                pos, dur = 0.0, 0.0
            return self._place_series_episode_line(season, number, sv.Progress(pos, dur))
        year = sv.year_of(it.get("releaseDate"), it.get("releasedate"), it.get("year"))
        rating = sv.rating_of(it.get("rating"))
        parts = [year, f"★ {rating:.1f}" if rating else ""]
        if not any(parts):
            genre = str(it.get("genre") or "").replace("/", ",").split(",")[0].strip()
            parts = [genre]
        return " · ".join(p for p in parts if p)

    @staticmethod
    def _place_series_se(season: int | None, number: int | None) -> str:
        if season is not None and number is not None:
            return tr("sp_se", s=season, e=number)
        if number is not None:
            return tr("sp_episode_short", n=number)
        return ""

    def _place_series_episode_line(self, season: int | None, number: int | None,
                                   prog: sv.Progress) -> str:
        se = self._place_series_se(season, number)
        left = prog.minutes_left if prog.started else 0
        if left:
            words = tr("home_min_left", n=left)
            return f"{se} · {words}" if se else words
        return se

    def _place_series_pills(self, it: dict) -> tuple:
        """A followed series with episodes it had not before: New episode;
        one with episodes downloaded: Downloaded (mw_downloads)."""
        sid = it.get("series_id")
        if sid is None or it.get("_kind") == "episode":
            return ()
        has = getattr(self, "_dl_series_has", None)
        dl = (("dl", ""),) if callable(has) and has(it) else ()
        store = getattr(self, "followed", None)
        try:
            if store is not None and store.is_followed(sid) and store.new_ids(sid):
                return (("new", tr("pill_new_episode")),) + dl
        except Exception:
            return dl
        return dl

    def _place_series_art_changed(self, *_a) -> None:
        """The list's rows drawn again (posters found, a colour set): the
        cards on screen and the rows' pictures likewise."""
        place = self._place_series
        if place is None or sip.isdeleted(place) or not self._place_series_active():
            return
        place.grid.refresh_art()
        for row in (place.popular, place.cont):
            changed = False
            for card in row.cards():
                src = card.data.series if isinstance(card.data, sc.Entry) else card.data
                url = self._place_series_art(src) if isinstance(src, dict) else ""
                if url and url != card.image:
                    card.image = url
                    changed = True
            if changed:
                row.strip._ahead = None
                row.strip._ask_ahead()
                row.strip.viewport().update()

    # -- the header ---------------------------------------------------------------------

    def _place_series_cat(self):
        cur = self.cat_list.currentItem()
        return cur, (cur.data(Qt.ItemDataRole.UserRole) if cur is not None else None)

    def _place_series_head(self) -> None:
        place = self._place_series
        if place is None:
            return
        cur, cat = self._place_series_cat()
        if cat is None:
            title = tr("cat_all")
        elif cat in _TITLE_KEYS:
            title = tr(_TITLE_KEYS[cat])
        else:
            title = cur.text().strip() if cur is not None else tr("nav_series")
        h = place.header
        h.set_title(title)
        n = place.grid.model_.rowCount()
        busy = bool(getattr(self, "_busy_main", None))
        sorts = self._place_series_sorts()
        order = self._place_series_order()
        raw = self._current_sort_raw()
        count = tr("count_episodes" if cat == "__continue__" else "count_series", n=n)
        if n:
            h.set_count(count)          # the sort's button names the order
        else:
            h.set_count("" if busy else count)
        h.set_sort(sorts, raw if raw in dict(sorts) else "global", order_words(order, True))
        h.set_views([("poster", tr("card_grid")), ("list", tr("card_list"))],
                    place.grid.layout_kind)
        place.filter.setPlaceholderText(self.search.placeholderText()
                                        or tr("list_filter_placeholder"))
        place.set_note(str(self._busy_main) if busy and not n else "")

    def _place_series_sorts(self) -> list[tuple[str, str]]:
        """The sort menu: what every category follows, named with the order
        that is here, then the orders a category can have of its own."""
        return ([("global", tr("sort_as_all", order=order_words(self._place_series_global(), True)))]
                + [(k, order_words(k, True)) for k in ("recent", "alpha_asc", "alpha_desc", "default")])

    def _place_series_global(self) -> str:
        """The order a category without one of its own is in: recently
        updated first - where the source says when, its own order else."""
        order = self._global_sort_order()
        if order == "recent":
            rows = self.list_model.items()[:200]
            if not any(r.get("last_modified") or r.get("added") for r in rows
                       if isinstance(r, dict)):
                return "default"
        return order

    def _place_series_order(self) -> str:
        """The order the cards are in: the category's own, else the one
        every category follows."""
        raw = self._current_sort_raw()
        order = self._place_series_global() if raw == "global" else raw
        return order if order in ORDER_WORDS else "default"

    def _place_series_sort(self, key: str) -> None:
        """An order picked: this category's own from now on, as the list's
        sort box keeps it."""
        self.settings.setValue(self._sort_setting_key(), key)
        self._sync_sort_box()
        self._apply_filter()

    def _place_series_view(self, key: str) -> None:
        place = self._place_series
        if place is None or key not in ("poster", "list"):
            return
        pick = getattr(self, "_place_view_pick", None)
        if callable(pick):
            pick("series", key, VIEW_KEY)       # not over the own while Just the list
        else:
            self.settings.setValue(VIEW_KEY, key)
        place.grid.set_layout(key)
        self._place_series_head()

    def _place_series_filter(self, text: str) -> None:
        """Typed in the place's filter: the list's, which filters (as it
        always did, a moment after the last key)."""
        if self.search.text() != text:
            self.search.setText(text)

    def _place_series_search_changed(self, text: str) -> None:
        place = self._place_series
        if place is None or sip.isdeleted(place) or place.filter.text() == text:
            return
        place.filter.blockSignals(True)
        try:
            place.filter.setText(text)
        finally:
            place.filter.blockSignals(False)

    # -- the rows over the cards ------------------------------------------------------

    def _place_series_rows(self) -> None:
        """Popular right now and Continue: over a category's series and over
        All, not over a list of its own (Continue watching, Recently added,
        Following) nor while the filter has words in it."""
        place = self._place_series
        if place is None or sip.isdeleted(place):
            return
        _cur, cat = self._place_series_cat()
        show = not self.search.text().strip() and cat not in _OWN_LISTS
        try:
            if show:
                self._place_series_popular()
            else:
                self._place_series_put_row("popular", place.popular, [])
        except Exception:
            log.exception("series place: Popular right now was not shown")
        row_shown = getattr(self, "_place_row_shown", None)
        cont_on = not callable(row_shown) or row_shown("series_continue")
        try:
            cont = self._place_series_continue_cards() if show and cont_on else []
        except Exception:
            log.exception("series place: Continue was not shown")
            cont = []
        self._place_series_put_row("cont", place.cont, cont)

    def _place_series_rows_soon(self) -> None:
        if self._place_series is None or self._place_series_rows_pending:
            return
        self._place_series_rows_pending = True

        def run() -> None:
            self._place_series_rows_pending = False
            if self._place_series_active():
                self._place_series_rows()
                self._place_series_mark()
        QTimer.singleShot(0, run)

    def _place_series_put_row(self, name: str, row, cards: list) -> None:
        """*cards* on *row* - only when they say something else than it
        does, and with the grid kept where it was on screen when the row
        comes or goes."""
        sigs = self._place_series_sigs if self._place_series_sigs is not None else {}
        self._place_series_sigs = sigs
        sig = tuple((c.key, c.title, c.line, c.rank, c.pills,
                     None if c.progress is None else round(c.progress, 3)) for c in cards)
        if sigs.get(name) == sig and row.isHidden() == (not cards):
            return
        sigs[name] = sig
        place = self._place_series
        bar = place.area.verticalScrollBar() if place.area is not None else None
        v = bar.value() if bar is not None else 0
        y0 = place.grid.y()
        row.set_cards(cards)
        if bar is not None and v > 0:
            lay = place.body.layout()
            if lay is not None:
                lay.activate()
            dy = place.grid.y() - y0
            if dy:
                bar.setValue(v + dy)

    # Popular right now ------------------------------------------------------------

    def _place_series_popular(self) -> None:
        """Popular right now: the source's series on this week's chart
        (mw_popular: matched on a worker, nothing hidden, locked or for
        adults); its row once they come. Nothing while it is switched off
        (Settings > Posters & info) or TMDB is."""
        place = self._place_series
        if (not self.popular_on("series")
                or getattr(getattr(self, "cover", None), "resolver", None) is None):
            self._place_series_put_row("popular", place.popular, [])
            return
        if not self._place_series_popular_watched:
            # A new chart, another source: asked again.
            self._place_series_popular_watched = True
            self.popular_watch(self._place_series_rows_soon)
        self.popular_titles("series", partial(self._place_series_popular_in, self.client),
                            limit=POPULAR_ASK)

    def _place_series_popular_in(self, client, rows: list) -> None:
        """The chart's series the source has, in its order: ranked on the
        row - in a category, that category's."""
        place = self._place_series
        if (client is not self.client or place is None or sip.isdeleted(place)
                or not self._place_series_active()):
            return
        _cur, cat = self._place_series_cat()
        if self.search.text().strip() or cat in _OWN_LISTS:
            rows = []
        picks = [r for r in rows or [] if isinstance(r, dict) and r.get("series_id") is not None
                 and (cat is None or str(r.get("category_id")) == str(cat))][:POPULAR_MAX]
        name = self._display_name_fn()
        cards = [SeriesCard(key=series_key(r), title=name(r), line=self._place_series_line(r),
                            image=self._place_series_art(r), rank=n + 1, data=r)
                 for n, r in enumerate(picks)]
        self._place_series_put_row("popular", place.popular, cards)

    # Continue -------------------------------------------------------------------------

    def _place_series_entries(self) -> list[sc.Entry]:
        """The series under way (core/series_continue.py), from what the app
        keeps: the resume points, History, the series page's cache."""
        cache = getattr(self, "_sp_cache", None) or {}
        client = self.client

        def episodes_of(series: dict) -> list[dict] | None:
            hit = cache.get((id(client), str(series.get("series_id"))))
            if hit is None:
                return None
            info = hit[1] if isinstance(hit, tuple) and len(hit) > 1 else None
            return self._episode_rows(info, series) if isinstance(info, dict) else None

        keep = self._catalog_filter("series")
        kids = getattr(self, "_kids_only", None)
        history = list(self.history.items())
        if callable(kids):
            history = kids(history, "series")
        return sc.gather(self._continue_items("episode"), history,
                         episodes_of=episodes_of, state_of=self._sp_episode_progress,
                         keep=lambda s: bool(keep(s)))

    def _place_series_continue_cards(self) -> list[SeriesCard]:
        name = self._display_name_fn()
        cards = []
        for e in self._place_series_entries():
            season, number = e.season_episode()
            pills: tuple = ()
            if e.resume:
                line = self._place_series_episode_line(season, number, e.progress)
            else:
                line = self._place_series_se(season, number)
                pills = (("seen", ""),) if e.watched else (("next", ""),)
            cards.append(SeriesCard(
                key=f"s:{e.series_id}", title=name(e.series), line=line,
                image=self._place_series_art(e.series),
                progress=e.progress.fraction if e.resume else None, pills=pills, data=e))
        return cards

    def _place_series_continue(self, card) -> None:
        """Continue: the episode plays through the window's own path, the
        series as its context, from where it got to without the resume
        question (as the series page's Continue); the place stays. The last
        one watched, its next not known here: the series' page works it
        out."""
        entry = getattr(card, "data", None)
        if not isinstance(entry, sc.Entry):
            return
        if entry.watched and not entry.known:
            self._open_series_page(entry.series)
            return
        ep = entry.episode
        at = entry.progress.pos if entry.resume and entry.progress.pos > 0 else None
        with self._from_pane():
            if ep.get("id") is not None:
                self._play_series_episode(entry.series, entry.seasons, ep, at)
                return
            # No episode id (a row played from its address): as Continue
            # watching plays it, where it got to.
            if at is not None:
                self._sp_start_once = (self._item_key(ep), at)
            try:
                self._play_continue_episode(ep)
            finally:
                self._sp_start_once = None

    def _place_series_continue_menu(self, card, pos: QPoint) -> None:
        entry = getattr(card, "data", None)
        if not isinstance(entry, sc.Entry):
            return
        season, number = entry.season_episode()
        se = self._place_series_se(season, number)
        m = QMenu(self)
        if entry.watched and not entry.known:
            m.addAction(tr("sa_open_series"), partial(self._open_series_page, entry.series))
        else:
            words = tr("sp_continue" if entry.resume else "sp_play", episode=se) if se \
                else tr("card_continue")
            m.addAction(words, partial(self._place_series_continue, card))
            m.addAction(tr("sa_open_series"), partial(self._open_series_page, entry.series))
        m.addSeparator()
        key = self._item_key(entry.episode)
        if entry.resume and key is not None:
            m.addAction(tr("ctx_continue_remove"), partial(
                self._place_series_forget, {**entry.episode, "_kind": "episode"}))
        elif entry.watched and key is not None:
            m.addAction(tr("ctx_remove_from_history"), partial(
                self._place_series_unlist, key))
        self._add_follow_action(m, entry.series)
        m.exec(pos)
        self._place_series_rows_soon()

    def _place_series_forget(self, ep: dict) -> None:
        self._remove_continue(ep)
        self._place_series_rows_soon()

    def _place_series_unlist(self, key) -> None:
        self.history.remove(key, "episode")
        self._place_series_rows_soon()

    # -- what the cards do -------------------------------------------------------------

    def _place_series_activate(self, card) -> None:
        """A card clicked, or Enter: a series' page (Back: the place as it
        was); an episode of Continue watching's category plays."""
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        if self._place_series_sel_timer is not None:
            self._place_series_sel_timer.stop()     # its page says it all: no detail asked
        with self._from_pane():
            self.play_item(it)

    def _place_series_open(self, card) -> None:
        """A series of Popular right now: its page."""
        it = getattr(card, "data", None)
        if isinstance(it, dict) and it.get("series_id") is not None:
            self._open_series_page(it)

    def _place_series_current(self, card) -> None:
        """The card chosen by the keyboard: the detail pane follows once
        the arrows rest (the list's selection)."""
        t = self._place_series_sel_timer
        if t is not None and card is not None and not self._place_series_syncing:
            t.start()

    def _place_series_list_row(self, card) -> int | None:
        it = getattr(card, "data", None)
        row = (self._place_series_row_of or {}).get(id(it))
        if row is None or self.list_model.item_at(row) is not it:
            return None
        return row

    def _place_series_sync_selection(self) -> None:
        place = self._place_series
        if place is None or sip.isdeleted(place) or not self._place_series_showing():
            return
        row = self._place_series_list_row(place.grid.current_item())
        if row is None:
            return
        idx = self.list_model.index(row)
        if self.listw.currentIndex() == idx:
            return
        self._place_series_syncing = True
        try:
            self.listw.setCurrentIndex(idx)
        finally:
            self._place_series_syncing = False

    def _place_series_list_moved(self, cur, _prev=None) -> None:
        """The list's chosen row moved by itself (the middle pane follows
        what plays, a jump back to a series): the card follows."""
        if self._place_series_syncing or not self._place_series_active():
            return
        it = self.list_model.item_at(cur.row()) if cur.isValid() else None
        if not isinstance(it, dict):
            return
        key = series_key(it)
        grid = self._place_series.grid
        now = grid.current_item()
        if grid.row_of(key) < 0 or (now is not None and now.key == key):
            return
        QTimer.singleShot(0, partial(self._place_series_reveal, key))

    def _place_series_menu(self, card, pos: QPoint) -> None:
        """A card's menu: the list's own for its row - for every card
        selected with it (favourites and folders, Follow, Watch later,
        marks, TMDB, rename, hide)."""
        place = self._place_series
        row = self._place_series_list_row(card)
        if place is None or row is None:
            return
        chosen = place.grid.selected_items()
        if card not in chosen:
            chosen = [card]
        rows = [r for r in (self._place_series_list_row(c) for c in chosen) if r is not None]
        sm = self.listw.selectionModel()
        self._rmb_selecting = True
        self._place_series_syncing = True
        try:
            sm.clearSelection()
            for r in rows:
                sm.select(self.list_model.index(r), QItemSelectionModel.SelectionFlag.Select)
            sm.setCurrentIndex(self.list_model.index(row),
                               QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._rmb_selecting = False
            self._place_series_syncing = False
        self._list_item_menu(row, card.data, pos)

    def _place_series_popular_menu(self, card, pos: QPoint) -> None:
        """Popular right now's: the menu a series has in its list."""
        it = getattr(card, "data", None)
        if isinstance(it, dict) and it.get("series_id") is not None:
            self._list_item_menu(-1, it, pos)
            self._place_series_rows_soon()

    # -- what plays ----------------------------------------------------------------------

    def _place_series_mark(self) -> None:
        """What plays marked: its series' card (an episode's own in Continue
        watching's category) and its series on the rows."""
        place = self._place_series
        if place is None or sip.isdeleted(place):
            return
        now = self._now_playing()           # here or on a TV
        sid_key = ep_key = ""
        if now is not None and now.group == "episode":
            sid = (now.series_ctx or {}).get("series_id")
            if sid is not None:
                sid_key = f"s:{sid}"
            ep_key = f"e:{now.key}"
        grid = place.grid
        grid.set_playing(ep_key if ep_key and grid.row_of(ep_key) >= 0 else sid_key)
        place.popular.set_playing(sid_key)
        place.cont.set_playing(sid_key)

    def _place_series_playing(self) -> None:
        """_start_playback and Stop: the marks, and Continue a moment later
        (where the title before got to is kept by then)."""
        if self._place_series is None:
            return
        self._place_series_mark()
        if self._place_series_active():
            self._place_series_rows_soon()

    def _place_series_stack_changed(self, _i: int) -> None:
        """The place on screen again (Back from a series' page): Continue
        and the cards' pills as they are now - the grid, its scroll and its
        choice as they were."""
        if not self._place_series_showing():
            return
        self._place_series_rows()
        self._place_series_mark()
        self._place_series.grid.viewport().update()

    # -- the window around it ----------------------------------------------------------

    def _place_series_restyle(self) -> None:
        """A theme or a language: drawn again in it."""
        place = self._place_series
        if place is None or sip.isdeleted(place):
            return
        place.restyle()
        place.retranslate()
        self._place_series_sigs = {}
        if self._place_series_active():
            self._place_series_head()
            self._place_series_rows()
            place.grid.refresh_art()

    def _place_series_refresh(self) -> None:
        """mw_places: what the place shows changed (a profile, a source, a
        followed series, a sync) - its rows and its cards' pills again."""
        place = self._place_series
        if place is None or sip.isdeleted(place) or not self._place_series_active():
            return
        self._place_series_sigs = {}
        self._place_series_rows_soon()
        place.grid.viewport().update()

    def _place_series_source_changed(self) -> None:
        """Another source or profile: nothing of the one before is kept
        (its cards, its rows); the lists load anew."""
        self._place_series_row_of = None
        place = self._place_series
        if place is None or sip.isdeleted(place):
            return
        self._place_series_sigs = {}
        place.popular.set_cards([])
        place.cont.set_cards([])
        place.grid.set_items([])
