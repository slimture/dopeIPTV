"""MainWindow mixin: what the places show - room for the list.

The choices are core/places.py's, each profile its own (Popular right now
keeps the household's keys on Posters & info), and all of them are on
Settings > Appearance > What the places show; a change shows at once, and a
profile switch shows that person's:

* the sidebar's entries a person never uses taken out (Guide, Search, Local
  files, Radio, Recordings - never Mine, TV, Movies, Series): gone from the
  full and the folded sidebar and from the rail, their places still open
  from their shortcuts, the command palette and the menus; the hairline
  between the groups goes with the last entry under it;
* the entries folded into a compact block of icons at the sidebar's top
  (the chevron under them, ui/nav_widgets.PlacesLayout), the full sidebar's
  alone - the icon rail stays as it is;
* a row over a place's list hidden from its heading ("Hide this row":
  Recently watched, Continue, Popular right now, what records now, My
  stations): at once, with a passing note saying where it comes back;
* Just the list: no rows over TV, Movies, Series and Local files, every
  place's header compact (Recordings' and Radio's too), Movies and Series
  as rows and TV compact - what each opens as while it is on, never stored
  over the person's own choice; a pick on a place's own switch holds for
  the session.

A place adopts it in a few calls: its rows named (``CardRow.set_hide_key``
with a row of ``places.ROWS``, ``hide_requested`` to ``_place_row_hide``)
and filled only while ``_place_row_shown(name)``; its page made compact
with ``_place_look_adopt(page)`` (anything with ``set_compact``); its own
grid / list or density read through ``_place_view`` and kept through
``_place_view_pick``. Nothing here plays, fetches or opens a connection.
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import Qt

from ..core import places
from ..core.log import log
from ..i18n import tr
from .notices import hub_of


class _PlaceLookMixin:
    """MainWindow mixin: the sidebar's entries and fold, the rows over the
    places' lists, Just the list."""

    #: What a place's own switch picked while Just the list is on (this
    #: session only), by place; None: nothing picked.
    _jl_views: dict | None = None
    _jl_was: bool | None = None

    # -- the sidebar ----------------------------------------------------------------

    def _place_look_init(self) -> None:
        """At build: the entries the profile took out, the block folded."""
        if getattr(self, "_nav_avail", None) is None:
            self._nav_avail = {}
        self._jl_was = places.just_list(self.settings)
        self._apply_places_fold()

    def _apply_nav_visibility(self) -> None:
        """Each entry shown while its place has something (a source's TV,
        Movies, Series and the guide: main_window's probe) and the person
        has not taken it out; the hairline between the groups while the
        column has a lower group to set apart."""
        btns = getattr(self, "nav_btns", None)
        if not btns:
            return
        gone = set(places.hidden(self.settings))
        # A kids profile has no Local files and no Recordings: nothing says
        # what is in them (mw_profiles.KIDS_NEVER).
        if getattr(self, "_profile_kids", lambda: False)():
            gone |= {"local", "rec"}
        avail = getattr(self, "_nav_avail", None) or {}
        for key, b in btns.items():
            place = places.PLACE_OF_NAV.get(key, key)
            b.setVisible(bool(avail.get(key, True)) and place not in gone)
        rule = getattr(self, "_places_rule", None)
        if rule is not None:
            lay = getattr(self, "_places_lay", None)
            folded = lay is not None and lay.is_folded()
            rule.setVisible(not folded and places.lower_shown(self.settings))

    def _toggle_places_fold(self) -> None:
        """The chevron under the places: folded into their compact block, or
        in full again - kept for the profile; the entry lit stays lit."""
        on = not places.folded(self.settings)
        self.settings.setValue(places.FOLDED_KEY, "true" if on else "false")
        log.info("places: %s", "folded" if on else "in full")
        btn = getattr(self, "_places_fold_btn", None)
        had = btn is not None and btn.hasFocus()
        self._apply_places_fold()
        if had:
            btn.setFocus(Qt.FocusReason.OtherFocusReason)

    # -- the rows over the lists --------------------------------------------------

    def _place_row_shown(self, name: str) -> bool:
        """Whether row *name* (core/places.ROWS) shows now."""
        return places.row_shown(self.settings, name)

    def _place_row_hide(self, name: str) -> None:
        """"Hide this row" from a row's heading: hidden at once and kept
        (Popular right now in its own key on Posters & info), and a passing
        note says where it comes back."""
        row = places.ROWS.get(name)
        if row is None:
            return
        log.info("places: the row %s hidden from its heading", name)
        if places.set_row(self.settings, name, False) and not row.own:
            self._popular_changed()        # Movies' and Series' tops ask again
        self._places_look_changed("row")
        page = tr("set_page_appearance" if row.own else "set_page_posters")
        # Pointing the way the words read (Qt does not mirror it).
        where = f"{tr('settings_title')} {'‹' if self.isRightToLeft() else '›'} {page}"
        # A little longer than a passing word: it says where to look.
        hub_of(self).note(tr("row_hidden_note", row=tr(row.words), where=where),
                          ms=6000, key=f"row_hidden:{name}")

    # -- Just the list ---------------------------------------------------------------

    def _place_compact(self) -> bool:
        """The places' headers compact: Just the list."""
        return places.just_list(self.settings)

    def _place_view(self, place: str, stored: str) -> str:
        """What *place* opens as: the person's own choice (*stored*), or
        while Just the list is on its plain form (rows, compact) - or what
        its switch picked since, this session."""
        if not places.just_list(self.settings):
            return stored
        picked = (self._jl_views or {}).get(place)
        return picked or places.view_of(self.settings, place, stored)

    def _place_view_pick(self, place: str, value: str, key: str) -> None:
        """A place's own switch picked *value*: kept in *key* - or, while
        Just the list is on, for the session alone, so the person's own
        choice is there as it was once it is off."""
        if places.just_list(self.settings):
            if self._jl_views is None:
                self._jl_views = {}
            self._jl_views[place] = value
        else:
            self.settings.setValue(key, value)

    def _place_look_adopt(self, page) -> None:
        """A place's page just made: compact as Just the list says."""
        fn = getattr(page, "set_compact", None)
        if callable(fn):
            fn(self._place_compact())

    def _place_look_pages(self) -> list:
        out = []
        for name in ("_place_tv_top", "_films_place", "_place_series", "_lp_page", "_rp_page",
                     "_rap_page"):
            page = getattr(self, name, None)
            if page is not None and not sip.isdeleted(page):
                out.append(page)
        return out

    # -- shown again ------------------------------------------------------------------

    def _place_look_apply(self) -> None:
        """The sidebar and the places' pages as the profile's choices say:
        the entries, the fold, the headers, what Movies, Series and TV open
        as. Each place's rows are its own refresh's (_places_look_changed
        asks them)."""
        jl = places.just_list(self.settings)
        if jl != self._jl_was:
            self._jl_views = None           # a new period: the plain forms again
            self._jl_was = jl
        self._apply_places_fold()
        compact = self._place_compact()
        for page in self._place_look_pages():
            try:
                page.set_compact(compact)
            except Exception:
                log.exception("places: a header not made compact")
        try:
            films = getattr(self, "_pf_view", None)
            if films is not None and not sip.isdeleted(films):
                want = self._pf_layout()
                if films.layout_kind != want:
                    films.set_layout(want)
                self._pf_header()
            series = getattr(self, "_place_series", None)
            if series is not None and not sip.isdeleted(series):
                want = self._place_series_layout()
                if series.grid.layout_kind != want:
                    series.grid.set_layout(want)
                if self._place_series_active():
                    self._place_series_head()
            if getattr(self, "_place_tv_top", None) is not None and self._place_tv_active():
                self._place_tv_sync()
        except Exception:
            log.exception("places: a view not shown as Just the list says")

    def _places_look_changed(self, reason: str = "") -> None:
        """What the places show changed (Settings, a row's heading, a
        profile, sync): shown at once - the sidebar, the headers, the views,
        and each place's rows asked again."""
        self._place_look_apply()
        steps = (("tv", self._place_look_tv_rows), ("films", self._place_look_films_rows),
                 ("series", self._place_look_series_rows),
                 ("local", self._place_look_local_rows),
                 ("rec", self._place_look_rec_rows), ("radio", self._place_look_radio_rows))
        for place, fn in steps:
            try:
                fn()
            except Exception:
                log.exception("places: %s's rows not shown again (%s)", place, reason)

    def _place_look_tv_rows(self) -> None:
        if getattr(self, "_place_tv_top", None) is not None and self._place_tv_active():
            self._place_tv_recent()

    def _place_look_films_rows(self) -> None:
        # Popular right now asked again as well: what was told while it was
        # off (Just the list) was nothing.
        if getattr(self, "_films_place", None) is not None:
            self._pf_popular_changed()

    def _place_look_series_rows(self) -> None:
        place = getattr(self, "_place_series", None)
        if place is not None and not sip.isdeleted(place) and self._place_series_active():
            self._place_series_sigs = {}
            self._place_series_rows()

    def _place_look_local_rows(self) -> None:
        if getattr(self, "_lp_page", None) is not None:
            self._lp_cont_sig = None
            if self._place_local_active():
                self._lp_rows_refresh()

    def _place_look_rec_rows(self) -> None:
        if getattr(self, "_rp_page", None) is not None:
            self._rp_now_refresh()

    def _place_look_radio_rows(self) -> None:
        if getattr(self, "_rap_page", None) is not None and self._place_radio_active():
            self._rap_favs_refresh()
