"""MainWindow mixin: the Movies place - the source's films in the shared
form of every place (ui/media_cards.py): the categories in the left column
as before; in the middle pane the header (the category's name, "48 movies
· order", the filter, the sort, Grid / List - remembered), "Popular right
now" (TMDB's chart of the week matched to this source's catalogue,
services/popular.py, ranked), "Continue" (this source's films in
progress), then the category's films as posters or rows. A click picks a
film and the detail column shows it - Continue with how far it got among
it (ui/mw_detail.py); a double click or Enter plays it, as the list's
double click does; a card in Continue goes on from where it stopped.

The place is a view of the list the window always had: the category's rows
are the list model's (MainWindow._apply_filter fills it - hidden categories
and rows, a locked category and its PIN, a kids profile, the filter, the
sort), one card per row in the same order, and the list follows the card
picked (and the reverse): its right-click menu, its selection for several
favourites at once, the detail panel and the pane following what plays
work on it as they did. Nothing about a film's own row changes.

Fast with a category of 20,000 films: one view of uniform cells, a card
made for each row with its title and nothing else; its picture, line,
pills and progress worked out as it comes on screen (``FilmCardView.dress``)
and again only for those after a change; pictures through the window's
loaders, only for the cards on screen and a few past them. The chart is
matched against the catalogue on a worker, never on the window's thread.

``_place_films_show()`` shows the place - the places' hook (ui/mw_places.py:
the sidebar's Movies entry, Back from the Guide, Search or a series' page);
the Movies mode shows it too, whichever way it was reached.
``_place_films_refresh()`` shows its content again (a profile, a source).
Popular right now comes from the window's ``popular_titles`` (ui/mw_popular.py),
asked again when a new chart comes (``popular_watch``).
"""

from __future__ import annotations

from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QPoint, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QLineEdit, QSizePolicy

from ..core import film_view as fv
from ..core import series_view as sv
from ..core.log import log
from ..core.profiles import adult_item
from ..i18n import tr
from .media_cards import Card, CardDelegate, CardRow, CardView, PlacePage, item_marks
from .mw_pane_follow import from_pane
from .series_cards import minutes_text
from .theme import corner_radius

#: Grid or list, the Movies place's own (a layout, for the household).
VIEW_KEY = "films_view"
VIEWS = ("poster", "list")
#: The most titles on "Popular right now".
POPULAR_MAX = 18


class _FilmDelegate(CardDelegate):
    """A film's card, dressed before it paints; a colour the person gave the
    film (its menu's Set colour) under it."""

    def paint(self, p, option, index) -> None:
        v = self._view
        card = v.model_.item_at(index.row())
        if card is None:
            return
        v.dress(card)
        if v.marks is not None:
            # Read as it paints, like the tint: a star given from the menu
            # shows at once, without dressing the card again.
            card.marks = v.marks(card)
        tint = v.tint(card) if v.tint is not None else ""
        if tint:
            c = QColor(tint)
            c.setAlphaF(0.28)
            p.save()
            p.setRenderHint(p.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            r = option.rect
            if v.layout_kind != "list":
                r = r.adjusted(0, 0, 0, -v.metrics.gap)
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), corner_radius(12), corner_radius(12))
            p.restore()
        super().paint(p, option, index)


class FilmCardView(CardView):
    """The place's films: a ``CardView`` whose cards are dressed - picture,
    line, pills, progress - only as they come on screen (*dress*, the
    window's), and again after a change (``redress``). Ctrl- or
    Shift-click gathers several, as the list did, instead of opening one;
    a right click in such a selection keeps it."""

    OBJECT_NAME = "FilmCardView"

    def __init__(self, loader, dress, layout: str = "poster", parent=None) -> None:
        self._dress_fn = dress
        self._dressed: dict[int, int] = {}
        self._gen = 0
        #: (card) -> a colour of the person's for it, or "".
        self.tint = None
        #: (card) -> its marks (favourite, Watch later), or None for none.
        self.marks = None
        super().__init__(loader, layout, parent)
        self.setItemDelegate(_FilmDelegate(self))
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)

    def dress(self, card: Card) -> None:
        if self._dressed.get(id(card)) == self._gen:
            return
        self._dressed[id(card)] = self._gen
        try:
            self._dress_fn(card)
        except Exception:
            log.exception("movies: a card was not dressed")

    def redress(self) -> None:
        """Every card dressed again as it next paints (marks, posters)."""
        self._gen += 1
        self._dressed.clear()
        self._ahead = None
        self.viewport().update()
        QTimer.singleShot(0, self._ask_ahead)

    def dressed_count(self) -> int:
        return len(self._dressed)

    def set_items(self, items) -> None:
        self._dressed.clear()
        super().set_items(items)

    def wanted_urls(self, item) -> list[str]:
        self.dress(item)
        return super().wanted_urls(item)

    def _clicked(self, idx) -> None:
        mods = QApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                   | Qt.KeyboardModifier.MetaModifier):
            return                      # gathering several
        super()._clicked(idx)

    def contextMenuEvent(self, e) -> None:
        idx = self.indexAt(e.pos())
        it = self.model_.item_at(idx.row()) if idx.isValid() else None
        if it is None:
            return
        sm = self.selectionModel()
        if sm.isSelected(idx) and len(sm.selectedRows()) > 1:
            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
        else:
            self.setCurrentIndex(idx)
        self.menu.emit(it, e.globalPos())


class _PlaceFilmsMixin:
    """MainWindow mixin: the Movies place."""

    _films_place: PlacePage | None = None
    _pf_view: FilmCardView | None = None
    _pf_popular_row: CardRow | None = None
    _pf_continue_row: CardRow | None = None
    _pf_filter: QLineEdit | None = None
    _pf_syncing = False
    _pf_cat: Any = None
    _pf_text = ""
    _pf_keys: list | None = None
    #: (the client they are of, Popular right now's rows) as last told.
    _pf_popular: tuple | None = None
    _pf_popular_gen = 0
    _pf_refresh_timer: QTimer | None = None

    # -- where it is ------------------------------------------------------------------

    def _films_place_showing(self) -> bool:
        place = self._films_place
        stack = getattr(self, "_mid_stack", None)
        return place is not None and stack is not None and stack.currentWidget() is place

    def _place_films_show(self) -> bool:
        """The Movies place in the middle pane: the source's film
        categories in the left column, the place beside them - as it was
        left when it was there already (its category, scroll and card)."""
        if self.mode != "vod":
            self.switch_mode("vod")
        else:
            center = getattr(self, "_center_stack", None)
            if center is not None and center.currentIndex() != 0 and not self._tv_land_on(0):
                center.setCurrentIndex(0)
            self._close_guide_page()
            self._close_series_page()
            self._close_search_page()
        made = self._films_place is None
        place = self._ensure_films_place()
        if made:
            self._pf_list_changed()
        if not self._films_place_showing():
            self._mid_stack.setCurrentWidget(place)
        self._pf_entered()
        self._pf_popular_ask()
        if self._pf_view is not None:
            self._pf_view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _pf_entered(self) -> None:
        """The place is the one shown: its entry lit, kept for the next
        start (mw_places)."""
        entered = getattr(self, "_place_entered", None)
        if callable(entered):
            entered("films")
        else:
            self._check_nav(self._nav_key_for_mode("vod"))

    def _place_films_refresh(self) -> None:
        """The places' refresh hook: what the place shows read again (a
        profile, a source, a sync) - the rows asked for anew, the cards
        dressed again."""
        if self._films_place is None:
            return
        self._pf_popular = None
        self._pf_popular_ask()
        self._pf_refresh_soon()

    def _ensure_films_place(self) -> PlacePage:
        place = self._films_place
        if place is not None:
            return place
        place = PlacePage(tr("nav_movies"))
        place.setObjectName("FilmsPlace")
        h = place.header
        filt = QLineEdit()
        filt.setObjectName("Search")
        filt.setClearButtonEnabled(True)
        filt.setPlaceholderText(tr("list_filter_placeholder"))
        filt.setAccessibleName(tr("list_filter_placeholder"))
        # It gives way in a narrow pane (and so does the title): nothing in
        # the header may make the place wider than the pane - its grid ran
        # off the edge.
        filt.setMinimumWidth(96)
        filt.setMaximumWidth(240)
        filt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for label in (h.title, h.count):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        filt.setText(self.search.text())
        filt.textChanged.connect(self._pf_filter_changed)
        self.search.textChanged.connect(self._pf_search_changed)
        h.add_widget(filt)
        self._pf_filter = filt
        h.sort_changed.connect(self._pf_sort_changed)
        h.view_changed.connect(self._pf_view_changed)
        loader = self.logos
        pop = CardRow(tr("card_popular"), loader, picture="poster")
        # A click picks (the detail column shows it), a double click plays.
        pop.strip.click_plays = lambda: False
        pop.strip.current_item_changed.connect(self._pf_strip_picked)
        pop.activated.connect(self._pf_play_card)
        pop.menu.connect(self._pf_row_menu)
        cont = CardRow(tr("card_continue"), loader, picture="poster")
        cont.activated.connect(self._pf_continue_card)
        cont.menu.connect(self._pf_row_menu)
        # Each hidden from its heading (mw_place_look; Popular right now in
        # its own key on Posters & info).
        for row, name in ((pop, "films_popular"), (cont, "films_continue")):
            row.set_hide_key(name)
            row.hide_requested.connect(self._place_row_hide)
        place.add_row(pop)
        place.add_row(cont)
        self._pf_popular_row, self._pf_continue_row = pop, cont
        view = FilmCardView(loader, self._pf_dress, self._pf_layout())
        view.tint = self._pf_tint
        view.marks = lambda card: item_marks(
            self, card.data if isinstance(card.data, dict) else {}, "vod")
        view.click_plays = lambda: False
        view.activated.connect(self._pf_play_card)
        view.menu.connect(self._pf_menu)
        view.current_item_changed.connect(self._pf_current_changed)
        view.selectionModel().selectionChanged.connect(self._pf_selection_changed)
        place.set_content(view)
        self._pf_view = view
        self._films_place = place
        self._mid_stack.addWidget(place)
        self._mid_stack.currentChanged.connect(self._pf_mid_changed)
        self.list_model.dataChanged.connect(self._pf_marks_changed)
        self.listw.selectionModel().currentChanged.connect(self._pf_list_current)
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(60)
        t.timeout.connect(self._pf_refresh)
        self._pf_refresh_timer = t
        watch = getattr(self, "popular_watch", None)
        if callable(watch):
            watch(self._pf_popular_changed)
        self._place_look_adopt(place)          # compact while Just the list
        self._pf_header()
        return place

    def _pf_model_reset(self) -> None:
        """The list model filled again (MainWindow connects it at build):
        the place made the first time the Movies mode fills it - so the mode
        shows the place whichever way it is reached (a nav entry, a jump to
        what plays, Home) - then its cards. Never in the list's way."""
        try:
            if self._films_place is None:
                if self.mode != "vod" or self.series_ctx:
                    return
                self._ensure_films_place()
            self._pf_list_changed()
        except Exception:
            log.exception("movies: the place did not follow the list")

    # -- the middle pane follows the mode ---------------------------------------------

    def _pf_takes_over(self) -> bool:
        """Whether the place stands in for the Movies list: while it is
        the place's hook (mw_places) - one put in its stead, or none, leaves
        the list under the shared header."""
        fn = getattr(self, "_place_films_show", None)
        return getattr(fn, "__func__", None) is _PlaceFilmsMixin._place_films_show

    def _pf_mid_changed(self, index: int) -> None:
        """The list page back in the middle pane (a page closed) while the
        Movies mode is on: the place in its stead."""
        if (index != 0 or self.mode != "vod" or self.series_ctx or self._films_place is None
                or not self._pf_takes_over()):
            return
        self._mid_stack.setCurrentWidget(self._films_place)
        QTimer.singleShot(0, self._pf_focus_soon)

    def _pf_focus_soon(self) -> None:
        view = self._pf_view
        if view is None or sip.isdeleted(view) or not self._films_place_showing():
            return
        focus = QApplication.focusWidget()
        if focus is None or not focus.isVisible() or focus is self.listw:
            view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _pf_follow_mode(self) -> None:
        stack = getattr(self, "_mid_stack", None)
        place = self._films_place
        if stack is None or place is None:
            return
        if self.mode == "vod" and not self.series_ctx:
            if stack.currentIndex() == 0 and self._pf_takes_over():
                stack.setCurrentWidget(place)
            return
        if stack.currentWidget() is place:
            stack.setCurrentIndex(0)

    # -- the category's films ---------------------------------------------------------

    def _pf_layout(self) -> str:
        """Posters or rows: the person's own - rows while Just the list is
        on, unless the switch picked posters since (mw_place_look)."""
        v = str(self.settings.value(VIEW_KEY, "poster") or "poster")
        v = v if v in VIEWS else "poster"
        view = getattr(self, "_place_view", None)
        if callable(view):
            v = view("films", v)
        return v if v in VIEWS else "poster"

    def _pf_list_changed(self) -> None:
        """The list model filled again (a category, the filter, the sort, a
        row hidden): its rows as cards, one each, in its order. The same
        list again keeps the card picked and the place's scroll."""
        self._pf_follow_mode()
        view, place = self._pf_view, self._films_place
        if view is None or place is None:
            return
        if self.mode != "vod" or self.series_ctx:
            if view.model_.rowCount():
                self._pf_syncing = True
                try:
                    view.set_items([])
                finally:
                    self._pf_syncing = False
                self._pf_keys = None
            return
        model = self.list_model
        rows = [model.item_at(i) for i in range(model.rowCount())]
        name = self._display_name_fn()
        key = self._item_key
        cards = []
        for row in rows:
            k = key(row)
            cards.append(Card(key="" if k is None else str(k), title=name(row), data=row))
        keys = [c.key for c in cards]
        cat = getattr(self, "_current_cat", None)
        text = self.search.text()
        same = (self._pf_keys is not None and cat == self._pf_cat and text == self._pf_text)
        unchanged = same and keys == self._pf_keys
        bar = place.area.verticalScrollBar() if place.area is not None else None
        scroll = bar.value() if bar is not None else 0
        cur = view.current_item()
        cur_key = cur.key if cur is not None else ""
        self._pf_syncing = True
        try:
            view.set_items(cards)
            if same and cur_key and view.row_of(cur_key) >= 0:
                view.selectionModel().setCurrentIndex(
                    view.model_.index(view.row_of(cur_key), 0),
                    QItemSelectionModel.SelectionFlag.ClearAndSelect)
        finally:
            self._pf_syncing = False
        self._pf_keys, self._pf_cat, self._pf_text = keys, cat, text
        if view.currentIndex().isValid():
            self._pf_mirror_to_list()           # the detail panel stays on it
        if bar is not None:
            if unchanged:
                QTimer.singleShot(0, lambda v=scroll: self._pf_scroll_to(v))
            elif not same:
                bar.setValue(0)
        self._pf_mark_playing()
        self._pf_header()
        self._pf_rows_refresh()
        self._pf_popular_ask()

    def _pf_scroll_to(self, value: int) -> None:
        place = self._films_place
        if place is not None and place.area is not None and not sip.isdeleted(place):
            place.area.verticalScrollBar().setValue(value)

    def _pf_dress(self, card: Card) -> None:
        """A card's picture, line ("2021 · 1 h 52 min · ★ 7.4"), pills
        (New, Watched) and how far it got - worked out as it comes on
        screen."""
        row = card.data if isinstance(card.data, dict) else {}
        key = self._item_key(row)
        pos, dur, _at = self.resume.progress(key, "movie") if key is not None else (0, 0, 0)
        mins = fv.minutes_of(row, dur_secs=dur)
        rating = fv.rating_of(row)
        card.line = " · ".join(t for t in (fv.year_of(row), minutes_text(mins) if mins else "",
                                           f"★ {rating:.1f}" if rating else "") if t)
        card.image = self._pf_cover(row)
        pills = []
        if fv.is_new(row):
            pills.append("new")
        if self.watched_source(row, "vod"):
            pills.append("seen")
        card.pills = tuple(pills)
        prog = sv.Progress(float(pos or 0), float(dur or 0))
        card.progress = prog.fraction if prog.started else None

    def _pf_cover(self, row: dict) -> str:
        """The poster the list would paint for *row* - only once it may be
        fetched: the source's own waits for TMDB's answer, as the list's
        does (CoverArtService.should_fetch)."""
        try:
            url = self.cover.cover_url(row, "vod")
        except Exception:
            return ""
        if not url:
            return ""
        logos = self.logos
        if url in logos.cache or url in logos.waiting:
            return url
        return url if self.cover.should_fetch(url, row, "vod") else ""

    def _pf_tint(self, card: Card) -> str:
        row = card.data if isinstance(card.data, dict) else {}
        fg, bg = self.item_tint(row, "vod")
        return bg or fg or ""

    def _pf_mark_playing(self) -> None:
        key = ""
        if getattr(self, "_playing_group", None) == "vod" and self._playing_key is not None:
            key = str(self._playing_key)
        elif getattr(self, "_cast_group", None) == "vod" and getattr(self, "_cast_key", None):
            key = str(self._cast_key)
        for w in (self._pf_view, self._pf_popular_row, self._pf_continue_row):
            if w is not None:
                w.set_playing(key)

    # -- the header -------------------------------------------------------------------

    def _pf_cat_name(self) -> str:
        cat = getattr(self, "_current_cat", None)
        if cat is None:
            return tr("nav_movies")
        if cat == "__continue__":
            return tr("cat_continue")
        if cat == "__recent__":
            return tr("cat_recent")
        raw = next((c.get("category_name") for c in (getattr(self, "_raw_categories", None) or [])
                    if str(c.get("category_id")) == str(cat)), None)
        try:
            return self.overrides.display_name("vod", cat, raw or str(cat))
        except Exception:
            return str(raw or cat)

    def _pf_header(self) -> None:
        place = self._films_place
        if place is None:
            return
        h = place.header
        h.set_title(self._pf_cat_name())
        n = self._pf_view.model_.rowCount() if self._pf_view is not None else 0
        box = self.sort_box
        # The count alone: the sort's button names the order.
        h.set_count(tr("count_movies", n=n) if n else "")
        opts = [(str(box.itemData(i)), box.itemText(i)) for i in range(box.count())]
        # The menu made again only when its words changed: an order picked
        # in it refills the list, and with it this header.
        shown = self._sort_button_words()
        if opts != h._sort_opts:
            h.set_sort(opts, str(box.currentData() or ""), shown)
        else:
            h.set_sort_key(str(box.currentData() or ""), shown)
        views = [("poster", tr("card_grid")), ("list", tr("card_list"))]
        if views != [(k, b.text()) for k, b in zip(h.switch.keys(), h.switch.buttons, strict=False)]:
            h.set_views(views, self._pf_layout())
        else:
            h.set_view_key(self._pf_layout())

    def _pf_sort_changed(self, key: str) -> None:
        i = self.sort_box.findData(key)
        if i >= 0 and i != self.sort_box.currentIndex():
            self.sort_box.setCurrentIndex(i)     # the list's own: _inline_view_changed

    def _pf_view_changed(self, kind: str) -> None:
        if kind not in VIEWS or self._pf_view is None:
            return
        pick = getattr(self, "_place_view_pick", None)
        if callable(pick):
            pick("films", kind, VIEW_KEY)       # not over the own while Just the list
        else:
            self.settings.setValue(VIEW_KEY, kind)
        view = self._pf_view
        cur = view.current_item()
        view.set_layout(kind)
        if cur is not None:
            QTimer.singleShot(0, lambda k=cur.key: self._pf_view is not None
                              and self._pf_view.focus_key(k, take_focus=False))

    def _pf_filter_changed(self, text: str) -> None:
        if self.search.text() != text:
            self.search.setText(text)            # the list's own filter

    def _pf_search_changed(self, text: str) -> None:
        f = self._pf_filter
        if f is not None and f.text() != text:
            f.blockSignals(True)
            f.setText(text)
            f.blockSignals(False)

    # -- the rows over the films ------------------------------------------------------

    def _pf_rows_refresh(self) -> None:
        """Popular right now and Continue, from what is known now (the
        chart's match asked for when it is old); each hidden while empty,
        and while the filter is in use."""
        pop, cont = self._pf_popular_row, self._pf_continue_row
        if pop is None or cont is None:
            return
        quiet = bool(self.search.text().strip()) or self.mode != "vod"
        cat = getattr(self, "_current_cat", None)
        shown = getattr(self, "_place_row_shown", None)
        cont_on = not callable(shown) or shown("films_continue")
        self._pf_set_row(cont, [] if quiet or cat == "__continue__" or not cont_on
                         else self._pf_continue_cards())
        self._pf_set_row(pop, [] if quiet else self._pf_popular_cards())
        self._pf_mark_playing()

    @staticmethod
    def _pf_set_row(row: CardRow, cards: list[Card]) -> None:
        """*cards* on *row* - the same titles in the same order brought up
        to date in place (its sideways scroll and the card picked kept),
        else the row filled anew."""
        old = row.cards()
        if [c.key for c in old] != [c.key for c in cards]:
            row.set_cards(cards)
            return
        changed = False
        for was, now in zip(old, cards, strict=True):
            for field in ("title", "line", "image", "progress", "pills", "rank", "data"):
                value = getattr(now, field)
                if getattr(was, field) != value:
                    changed = True
                    setattr(was, field, value)
        if changed:
            row.strip.viewport().update()

    def _pf_continue_cards(self) -> list[Card]:
        keep = self._catalog_filter("vod")
        out: list[Card] = []
        for r in self._continue_items("vod"):
            if not keep(r):
                continue
            prog = sv.Progress(float(r.get("_resume_pos") or 0), float(r.get("_resume_dur") or 0))
            left = prog.minutes_left
            k = self._item_key(r)
            out.append(Card(key="" if k is None else str(k),
                            title=str(r.get("name") or r.get("title") or ""),
                            line=tr("home_min_left", n=left) if left else "",
                            image=self._pf_cover(r), progress=prog.fraction, data=r))
        return out

    def _pf_popular_cards(self) -> list[Card]:
        got = self._pf_popular
        if (got is None or got[0] is not self.client
                or getattr(getattr(self, "cover", None), "resolver", None) is None
                or not self._pf_popular_on()):
            return []
        # Looked at again here: a category hidden or locked since it was told.
        picked = [r for r in self._visible_catalog(got[1], "vod") if fv.is_movie(r)]
        out: list[Card] = []
        for i, r in enumerate(picked, 1):
            k = self._item_key(r)
            rating = fv.rating_of(r)
            line = " · ".join(t for t in (fv.year_of(r), f"★ {rating:.1f}" if rating else "") if t)
            out.append(Card(key="" if k is None else str(k),
                            title=str(r.get("name") or r.get("title") or ""), line=line,
                            image=self._pf_cover(r), rank=i,
                            pills=("seen",) if self.watched_source(r, "vod") else (),
                            data=r))
        return out

    def _pf_popular_on(self) -> bool:
        on = getattr(self, "popular_on", None)
        return bool(on("vod")) if callable(on) else True

    def _pf_popular_ask(self) -> None:
        """Popular right now asked of the window (matched on a worker; at
        once when it is known) - unless it is told for this source."""
        ask = getattr(self, "popular_titles", None)
        got = self._pf_popular
        if not callable(ask) or (got is not None and got[0] is self.client):
            return
        self._pf_popular_gen += 1
        gen, client = self._pf_popular_gen, self.client
        ask("vod", lambda rows: self._pf_popular_in(gen, client, rows), limit=POPULAR_MAX)

    def _pf_popular_in(self, gen: int, client, rows) -> None:
        if gen != self._pf_popular_gen or client is not self.client:
            return
        self._pf_popular = (client, [r for r in (rows or []) if isinstance(r, dict)])
        if self._films_place is not None and not sip.isdeleted(self._films_place):
            self._pf_rows_refresh()

    def _pf_popular_changed(self) -> None:
        """A new chart, or another source (popular_watch): asked again."""
        self._pf_popular = None
        if self._films_place is not None and not sip.isdeleted(self._films_place):
            self._pf_rows_refresh()
            self._pf_popular_ask()

    # -- the list follows the card picked (and the reverse) ---------------------------

    def _pf_current_changed(self, card) -> None:
        if self._pf_syncing or self._pf_view is None:
            return
        self._pf_mirror_to_list()

    def _pf_selection_changed(self, *_a) -> None:
        if self._pf_syncing or self._pf_view is None:
            return
        self._pf_mirror_to_list()

    def _pf_mirror_to_list(self) -> None:
        """The card picked - and every card gathered - picked in the list,
        whose menu, favourites and detail panel act on them."""
        view, model = self._pf_view, self.list_model
        if view is None or view.model_.rowCount() != model.rowCount():
            return
        sm = self.listw.selectionModel()
        sel = QItemSelection()
        for rng in view.selectionModel().selection():
            sel.select(model.index(rng.top()), model.index(rng.bottom()))
        cur = view.currentIndex()
        self._pf_syncing = True
        try:
            sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            if cur.isValid() and cur.row() != self.listw.currentIndex().row():
                sm.setCurrentIndex(model.index(cur.row()),
                                   QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._pf_syncing = False

    def _pf_list_current(self, cur, _prev=None) -> None:
        """A row picked in the list by the window (what plays, a jump to
        it): its card picked and on screen."""
        view = self._pf_view
        if self._pf_syncing or view is None or not cur.isValid():
            return
        if view.model_.rowCount() != self.list_model.rowCount():
            return
        self._pf_syncing = True
        try:
            idx = view.model_.index(cur.row(), 0)
            view.setCurrentIndex(idx)
        finally:
            self._pf_syncing = False

    # -- what a card does -------------------------------------------------------------

    def _pf_play_card(self, card) -> None:
        """A double click or Enter on a film: it plays, as the list's
        double click plays it (the resume question where it is under way);
        the place stays as it is."""
        row = card.data if isinstance(card, Card) else None
        if isinstance(row, dict) and self._pf_allowed(row):
            self._film_play(row)

    def _pf_continue_card(self, card) -> None:
        """Continue's card: on from where it stopped, without the question."""
        row = card.data if isinstance(card, Card) else None
        if isinstance(row, dict) and self._pf_allowed(row):
            pos, _dur = self._film_progress(row)
            self._film_play(row, pos if pos > 0 else None)

    def _pf_strip_picked(self, card) -> None:
        """A card picked in Popular right now: its film in the detail
        column (it may be of another category than the list's)."""
        row = card.data if isinstance(card, Card) else None
        if isinstance(row, dict) and not self._pf_syncing:
            self._current_key = self._item_key(row)
            self._show_detail(row)

    def _pf_allowed(self, row: dict) -> bool:
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

    def _film_progress(self, row: dict) -> tuple[float, float]:
        """(where it stopped, its length) in seconds - this computer's
        resume point or a media server's newer one."""
        key = self._item_key(row) if isinstance(row, dict) else None
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

    @from_pane
    def _film_play(self, row: dict, at: float | None = None) -> bool:
        """A film in the docked player, the middle pane left as it is,
        through the window's own path (resume points, marks, Trakt and
        Simkl, History, subtitles remembered, a media server's reports).
        *at*: where it starts, without the resume question (0: from the
        start; None: the question, as the list's double click asks it).
        False when nothing could start."""
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

    def _pf_menu(self, card, pos: QPoint) -> None:
        """A film's right click: the list's own menu, for the card (and the
        cards gathered with it)."""
        view = self._pf_view
        if not isinstance(card, Card) or view is None:
            return
        self._pf_mirror_to_list()
        row = view.currentIndex().row()
        if not (0 <= row < self.list_model.rowCount()
                and self.list_model.item_at(row) is card.data):
            row = -1
        # What it starts plays from the place: the place stays as it is.
        with self._from_pane():
            self._context_menu(None, item=card.data, row=row, at=pos)
        self._pf_refresh_soon()

    def _pf_row_menu(self, card, pos: QPoint) -> None:
        if isinstance(card, Card) and isinstance(card.data, dict):
            with self._from_pane():
                self._context_menu(None, item=card.data, at=pos)
            self._pf_rows_refresh()
            self._pf_refresh_soon()

    # -- drawn again ------------------------------------------------------------------

    def _pf_marks_changed(self, *_a) -> None:
        """The list's rows drawn again (a mark, a poster TMDB found, a
        colour): the cards too, a moment later, once."""
        if self._pf_refresh_timer is not None and self._pf_view is not None:
            self._pf_refresh_timer.start()

    def _pf_refresh_soon(self) -> None:
        if self._pf_refresh_timer is not None:
            self._pf_refresh_timer.start()
        else:
            QTimer.singleShot(0, self._detail_film_refresh)

    def _pf_refresh(self) -> None:
        view = self._pf_view
        if view is None or sip.isdeleted(view):
            return
        view.redress()
        self._pf_rows_refresh()
        self._detail_film_refresh()

    def _pf_playback_started(self, key, kind: str) -> None:
        """_start_playback: what plays marked; Continue with the film
        before it; the detail column's Continue drawn again."""
        try:
            if self._films_place is not None:
                self._pf_mark_playing()
                self._pf_refresh_soon()
        except Exception:
            log.exception("movies: what plays was not marked")

    def _pf_source_changed(self) -> None:
        """Another source (or profile): its films, its chart match."""
        self._pf_popular = None
        self._pf_popular_gen += 1
        self._pf_keys = None

    def _pf_restyle(self) -> None:
        place = self._films_place
        if place is not None:
            place.header.set_title(self._pf_cat_name())
            for row, title in ((self._pf_popular_row, "card_popular"),
                               (self._pf_continue_row, "card_continue")):
                if row is not None:
                    row.set_title(tr(title))
            if self._pf_filter is not None:
                self._pf_filter.setPlaceholderText(tr("list_filter_placeholder"))
                self._pf_filter.setAccessibleName(tr("list_filter_placeholder"))
            place.restyle()
            self._pf_header()
            if self._pf_view is not None:
                self._pf_view.redress()
            self._pf_rows_refresh()

    def _pf_state(self) -> dict:
        """What the place shows, for a test and the log."""
        view = self._pf_view
        return {"shown": self._films_place_showing(),
                "cards": view.model_.rowCount() if view is not None else 0,
                "layout": view.layout_kind if view is not None else "",
                "popular": [c.key for c in (self._pf_popular_row.cards()
                                            if self._pf_popular_row else [])],
                "continue": [c.key for c in (self._pf_continue_row.cards()
                                             if self._pf_continue_row else [])]}
