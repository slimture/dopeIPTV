"""MainWindow mixin: the Radio place (ui/radio_place.py draws it).

The ways in stay in the left column as they were (My stations, Most voted,
Most listened, Search all stations, Countries, Languages, Genres); the
middle pane shows, where the list was, the shared form of every place: a
way back to the countries, languages or genres while one is open ("Back to
Countries" - the mouse's back button, Backspace), the list's name with how
many stations (or countries, languages, genres) it holds, the filter (the
list's own - under Search it asks the whole directory, as ever), Grid /
List for stations (remembered, ``radio_view``); "My stations" over a list
of stations - the favourite stations as small cards, what the station
playing says is on as its line -, then the stations as rows or tiles with
their logos (through the window's image loaders, only for what shows), the
country, what it plays and how as a muted line, a favourite's star, the
one playing marked; or the countries (with their flags), languages and
genres, each with how many stations it has.

The list under it is the source of truth (ui/mw_radio.py: every request
to radio-browser.info, on the pool, as before - the place asks nothing of
its own): the place draws what it holds through ``ListMirror``
(ui/list_mirror.py) and the list follows the card picked, so the menus
(play, open elsewhere, favourite, its folders, the station's website), the
detail panel and the pane following what plays work as they did. A double
click or Enter plays a station (the visualiser, its ICY title) or opens a
country; a click is reported to the directory only when Settings >
Integrations says so, as before.

The place answers mw_places' hooks: ``_place_radio_show`` (the sidebar's
entry, Back from a page over it) and ``_place_radio_refresh`` (a profile,
a sync, a favourite station changed).
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QTimer

from ..i18n import tr
from ..providers import radio_browser as rb
from .list_mirror import ListMirror
from .list_place import PlaceCard, line_of
from .media_cards import MARK_FAV
from .mw_radio import CATEGORIES, FAVS, GROUP_KINDS, SEARCH, station_sub
from .radio_place import RadioPlace, flag_path, station_line

#: Stations as tiles or rows ("wide" / "list"), remembered.
VIEW_KEY = "radio_view"
VIEWS = ("wide", "list")
#: How many countries, languages or genres a list holds, in words.
GROUP_COUNT = {rb.COUNTRY: "radio_n_countries", rb.LANGUAGE: "radio_n_languages",
               rb.TAG: "radio_n_genres"}


class _PlaceRadioMixin:
    """MainWindow mixin: the Radio place."""

    _rap_page: RadioPlace | None = None
    _rap_mirror: ListMirror | None = None
    _radio_loading = False
    _radio_error = False

    # -- where it is ------------------------------------------------------------------

    def _place_radio_active(self) -> bool:
        place, stack = self._rap_page, getattr(self, "_list_stack", None)
        return (place is not None and stack is not None and not sip.isdeleted(place)
                and stack.currentWidget() is place)

    def _place_radio_showing(self) -> bool:
        return (self._place_radio_active()
                and self._mid_stack.currentWidget() is self._list_page)

    def _place_radio_show(self) -> bool:
        """The Radio place in the middle pane: mw_places' hook for the
        sidebar's entry and for Back from a page over it - the place as it
        was left. True."""
        self._close_guide_page()
        self._close_series_page()
        self._close_search_page()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        if self.mode != "radio":
            self.switch_mode("radio")
        else:
            self._place_radio_sync()
        entered = getattr(self, "_place_entered", None)
        if callable(entered):
            entered("radio")
        may = getattr(self, "_place_series_may_focus", None)
        if self._place_radio_showing() and (may is None or may()):
            self._rap_page.view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _place_radio_sync(self) -> None:
        """switch_mode: the place where the list is while Radio is the
        mode, the list itself otherwise."""
        stack = getattr(self, "_list_stack", None)
        if stack is None:
            return
        if self.mode == "radio":
            place = self._ensure_radio_place()
            if stack.currentWidget() is not place:
                stack.setCurrentWidget(place)
            self._rap_mirror.rebuild()
        elif self._rap_page is not None and stack.currentWidget() is self._rap_page:
            stack.setCurrentWidget(self._list_body)

    def _ensure_radio_place(self) -> RadioPlace:
        place = self._rap_page
        if place is not None and not sip.isdeleted(place):
            return place
        place = RadioPlace(self.logos)
        self._rap_page = place
        v = place.view
        v.dress_fn = self._rap_dress
        v.activated.connect(self._rap_activate)
        v.menu.connect(self._rap_menu)
        place.favs.strip.click_plays = self._autoplay_preview   # as the lists
        place.favs.activated.connect(self._rap_fav_activate)
        place.favs.menu.connect(self._rap_fav_menu)
        # My stations hidden from its heading (mw_place_look); the header
        # compact while Just the list.
        place.favs.set_hide_key("radio_favs")
        place.favs.hide_requested.connect(self._place_row_hide)
        self._place_look_adopt(place)
        place.back_requested.connect(self._rap_back)
        place.layout_changed.connect(self._rap_layout_picked)
        self._rap_mirror = ListMirror(self, place, self._rap_card_of, self._place_radio_active,
                                      self._rap_context, self._rap_after)
        self._mid_stack.currentChanged.connect(self._rap_stack_changed)
        self._list_stack.addWidget(place)
        return place

    # -- what the cards are of --------------------------------------------------------------

    def _rap_cat(self):
        return getattr(self, "_current_cat", None)

    def _rap_groups(self) -> bool:
        """A list of countries, languages or genres (none open)."""
        return self._rap_cat() in GROUP_KINDS and self._radio_group is None

    def _rap_context(self) -> tuple:
        return (self._rap_cat(), self._radio_group, self.search.text().strip())

    def _rap_card_of(self, it: dict) -> PlaceCard | None:
        if it.get("_header"):
            return None
        return PlaceCard(key=str(it.get("_key") or it.get("url") or it.get("name") or ""),
                         title=str(it.get("name") or ""), data=it, fit=True)

    def _rap_layout(self) -> tuple[str, str]:
        if self._rap_groups():
            return "list", "wide"
        v = str(self.settings.value(VIEW_KEY, "list") or "list")
        return (v if v in VIEWS else "list"), "wide"

    def _rap_after(self) -> None:
        place = self._rap_page
        if place is None or sip.isdeleted(place):
            return
        place.set_layout(*self._rap_layout())
        self._rap_head()
        self._rap_favs_refresh()
        self._rap_mark()

    # -- what a card says ---------------------------------------------------------------------

    def _rap_playing_key(self) -> str:
        return self._now_key("radio")       # here or on a TV

    def _rap_dress(self, card: PlaceCard) -> None:
        it = card.data if isinstance(card.data, dict) else {}
        card.title = str(it.get("name") or card.title)
        card.fit = True
        if it.get("_kind") == "radiogroup":
            kind, value = (list(it.get("_group") or []) + ["", ""])[:2]
            card.image = flag_path(value) if kind == rb.COUNTRY else ""
            card.line = line_of([tr("count_stations", n=it.get("_count", 0))])
            card.marks = ()
            return
        card.image = str(it.get("stream_icon") or "")
        now_on = self._radio_icy_now if card.key and card.key == self._rap_playing_key() else ""
        card.line = line_of([station_line(str(it.get("_sub") or station_sub(it)), now_on)])
        fav = self._rap_cat() != FAVS and self._radio_is_fav(it)
        card.marks = (MARK_FAV,) if fav else ()

    # -- the header ---------------------------------------------------------------------------

    def _rap_list_name(self) -> str:
        cat = self._rap_cat()
        key = next((k for k, data in CATEGORIES if data == cat), "")
        return tr(key) if key else tr("nav_radio")

    def _rap_count(self) -> str:
        place = self._rap_page
        n = place.view.model_.rowCount() if place is not None else 0
        if not n:
            return ""
        if self._rap_groups():
            return tr(GROUP_COUNT.get(self._rap_cat(), "count_stations"), n=n)
        return tr("count_stations", n=n)

    def _rap_head(self) -> None:
        place = self._rap_page
        if place is None or sip.isdeleted(place):
            return
        h = place.header
        grp = self._radio_group
        h.set_title(grp[2] if grp else self._rap_list_name())
        h.set_count(self._rap_count())
        place.set_back(tr("fp_back_to", name=self._rap_list_name()) if grp else None)
        stations = not self._rap_groups()
        place.show_layout_switch(stations, self._rap_layout()[0] if stations else "")
        mirror = self._rap_mirror
        if mirror is not None and place.filter.text() != self.search.text():
            mirror.put_filter(self.search.text())
        hint = self.search.placeholderText() or tr("list_filter_placeholder")
        if place.filter.placeholderText() != hint:
            place.filter.setPlaceholderText(hint)
        place.set_note(self._rap_note())

    def _rap_note(self) -> str:
        place = self._rap_page
        if place is not None and getattr(self, "_offline", lambda: False)():
            return tr("offline_radio")     # offline mode: a calm word (mw_offline)
        if place is None or place.view.model_.rowCount() or self._radio_loading:
            return ""
        if self._radio_error:
            return tr("radio_unreachable")
        cat, text = self._rap_cat(), self.search.text().strip()
        if cat == FAVS:
            return tr("tmdb_no_results") if text else tr("radio_favs_empty")
        if cat == SEARCH and self._radio_group is None:
            return tr("radio_search_hint") if len(text) < 2 else tr("radio_search_none")
        return tr("tmdb_no_results") if text else tr("radio_none")

    def _rap_layout_picked(self, key: str) -> None:
        if key not in VIEWS:
            return
        self.settings.setValue(VIEW_KEY, key)
        place = self._rap_page
        if place is None:
            return
        cur = place.view.current_item()
        place.set_layout(*self._rap_layout())
        if cur is not None:
            QTimer.singleShot(0, lambda k=cur.key: self._rap_page is not None
                              and self._rap_page.view.focus_key(k, take_focus=False))

    # -- My stations ----------------------------------------------------------------------------

    def _rap_fav_cards(self) -> list[PlaceCard]:
        """The favourite stations as small cards - over a list of stations
        other than My stations itself, while nothing is typed."""
        if self._rap_cat() == FAVS or self._rap_groups() or self.search.text().strip():
            return []
        if getattr(self, "_offline", lambda: False)():
            return []                   # offline mode: none of them plays
        playing = self._rap_playing_key()
        out = []
        for it in self._radio_fav_rows():
            key = str(it.get("_key") or "")
            now_on = self._radio_icy_now if key and key == playing else ""
            out.append(PlaceCard(key=key, title=str(it.get("name") or ""),
                                 line=line_of([station_line(str(it.get("_sub") or ""), now_on)]),
                                 image=str(it.get("stream_icon") or ""), data=it, fit=True))
        return out

    def _rap_favs_refresh(self) -> None:
        place = self._rap_page
        if place is None or sip.isdeleted(place):
            return
        shown = getattr(self, "_place_row_shown", None)
        cards = self._rap_fav_cards() if not callable(shown) or shown("radio_favs") else []
        old = place.favs.cards()
        if old and [c.key for c in old] == [c.key for c in cards]:
            for a, b in zip(old, cards, strict=True):
                a.title, a.line, a.image, a.data = b.title, b.line, b.image, b.data
            place.favs.strip.viewport().update()
        else:
            place.favs.set_cards(cards)
        place.favs.set_playing(self._rap_playing_key())

    # -- what the cards do ----------------------------------------------------------------------

    def _rap_activate(self, card) -> None:
        """A double click, or Enter: a station plays, a country opens."""
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        if self._rap_mirror is not None:
            self._rap_mirror.settle()
        with self._from_pane():
            self.play_item(it)

    def _rap_fav_activate(self, card) -> None:
        it = getattr(card, "data", None)
        if isinstance(it, dict):
            with self._from_pane():
                self._radio_play(it)

    def _rap_menu(self, card, pos: QPoint) -> None:
        """A card's menu: the list's own for a station or a country."""
        if self._rap_mirror is not None:
            self._rap_mirror.settle()
        self._rap_fav_menu(card, pos)

    def _rap_fav_menu(self, card, pos: QPoint) -> None:
        it = getattr(card, "data", None)
        if isinstance(it, dict):
            with self._from_pane():
                self._radio_context_menu(None, it, at=pos)

    def _rap_back(self) -> None:
        """Back, the mouse's back button, Backspace: out of a country,
        language or genre to their list."""
        if self.mode == "radio" and self._radio_group is not None:
            self._radio_up()

    # -- what plays ---------------------------------------------------------------------------

    def _rap_mark(self) -> None:
        place = self._rap_page
        if place is None or sip.isdeleted(place):
            return
        key = self._rap_playing_key()
        place.view.set_playing(key)
        place.favs.set_playing(key)

    def _place_radio_playing(self) -> None:
        """_start_playback and Stop: what plays marked."""
        if self._rap_page is None:
            return
        self._rap_mark()
        if self._place_radio_active():
            self._rap_page.view.redress()
            self._rap_favs_refresh()

    def _place_radio_icy(self) -> None:
        """What the station playing says is on changed: its lines say it."""
        if self._rap_page is None or not self._place_radio_active():
            return
        self._rap_page.view.redress()
        self._rap_favs_refresh()

    def _rap_stack_changed(self, _i: int) -> None:
        if self._place_radio_showing():
            self._rap_favs_refresh()
            self._rap_mark()
            self._rap_page.view.redress()

    # -- the window around it -----------------------------------------------------------------

    def _place_radio_restyle(self) -> None:
        place = self._rap_page
        if place is None or sip.isdeleted(place):
            return
        place.restyle()
        place.retranslate()
        if self._place_radio_active():
            self._rap_head()
            self._rap_favs_refresh()
            place.view.redress()

    def _place_radio_refresh(self) -> None:
        """mw_places: what the place shows changed (a profile, a sync, a
        favourite station) - My stations and the stars again."""
        place = self._rap_page
        if place is None or sip.isdeleted(place) or not self._place_radio_active():
            return
        self._rap_favs_refresh()
        place.view.redress()
