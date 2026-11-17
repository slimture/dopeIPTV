"""The TV place in the window (MainWindow mixin).

The source's categories stay in the left column; the middle pane is the
place: its header (ui/media_cards.PageHeader: the category's name,
"48 channels · by number", the sort, Compact / Airy), Recently watched (this
source's channels played last, as small cards with what is on now; hidden
when there are none), then the channels as rows - the very list every other
part of the app knows (``listw``: zapping, numbers typed, the channel's menu,
favourites and their drag and drop, stream health, a box's tuners, Nerd's
numbers and technical line, the filter, multiselect, the phone remote's and
TV mode's use of it), its rows painted the place's way by
ui/channel_list.ChannelDelegate through ui/tv_place.paint_tv_row.

``_place_tv_show()`` shows it - mw_places' hook for TV (its entry, Back from
the Guide, Search or a series' page), ``_place_tv_refresh()`` shows it again
(a profile, a source, sync); the places' shared header gives way to the
place's own while it shows. The rows' density is the profile's own
(``tv_density``, core/profiles.OWN_KEYS), kept by the switch; the sort is
the list's own per-category order (the same as the control strip's,
mw_sort). What is on comes from the guide in memory - never a download for
painting (``tv_place.guide_now_next``).

Recently watched is History's channels of this source (each source keeps
its own history), newest first: none hidden by hand, none of a hidden
category or of a locked one while it is locked, none a kids profile may not
see; a channel played in private viewing never gets there (History keeps
nothing of it). A card plays as its row in the list does when the list
holds it, else as a channel played from the pane (mw_pane_follow).
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtWidgets import QAbstractItemView, QMenu

from ..core.log import log
from ..i18n import tr
from .category_flags import category_flag
from .media_cards import Card, initials_of
from .mw_pane_follow import from_pane
from .tv_place import DENSITIES, TvPlaceTop, guide_now_next

#: Where the switch keeps the rows' density (the profile's own).
DENSITY_KEY = "tv_density"
#: The most cards Recently watched shows.
RECENT_MAX = 12
#: How often what is on is looked at again (the rows' bars, the cards' lines).
TICK_MS = 30_000
#: The order the count line names, by the list's effective sort order.
_ORDER_WORDS = {"default": "tv_by_number", "alpha_asc": "tv_by_name",
                "alpha_desc": "tv_by_name_desc", "recent": "tv_by_added"}


class _PlaceTvMixin:
    """MainWindow mixin: the TV place."""

    _place_tv_top: TvPlaceTop | None = None
    _place_tv_timer: QTimer | None = None
    _place_tv_known: dict | None = None
    _place_tv_known_of: Any = None
    _place_tv_known_rows: Any = None
    _place_tv_hid_header = False

    # -- built once ---------------------------------------------------------------

    def _place_tv_init(self) -> None:
        """The header and Recently watched over the list (_build_ui, once the
        list page is whole); hidden until the TV place shows."""
        top = TvPlaceTop(self.logos)
        top.hide()
        top.sort_changed.connect(self._place_tv_sort)
        top.density_changed.connect(self._place_tv_density)
        top.activated.connect(self._place_tv_play_card)
        top.menu.connect(self._place_tv_card_menu)
        top.recent.set_line_of(self._place_tv_card_line)
        # Recently watched hidden from its heading (mw_place_look), and the
        # header compact while Just the list is on.
        top.hide_requested.connect(self._place_row_hide)
        self._place_look_adopt(top)
        self._list_page.layout().insertWidget(0, top)
        self._place_tv_top = top
        self.delegate.set_tv_density(self._place_tv_density_now())
        self._place_tv_timer = QTimer(self)
        self._place_tv_timer.setInterval(TICK_MS)
        self._place_tv_timer.timeout.connect(self._place_tv_tick)
        # Stop: no card marked any more (after _on_player_stopped).
        player = getattr(self, "player", None)
        if player is not None:
            player.stopped.connect(self._place_tv_playing_changed)

    def _place_tv_density_now(self) -> str:
        """The rows' density: the profile's own - compact while Just the
        list is on, unless the switch picked another since (mw_place_look)."""
        d = str(self.settings.value(DENSITY_KEY, "compact") or "compact")
        d = d if d in DENSITIES else "compact"
        view = getattr(self, "_place_view", None)
        if callable(view):
            d = view("tv", d)
        return d if d in DENSITIES else "compact"

    # -- where it is ----------------------------------------------------------------

    def _place_tv_active(self) -> bool:
        """The middle list is the TV place's: the live channels."""
        return self.mode == "live" and not self.series_ctx

    def _place_tv_show(self) -> bool:
        """The TV place in the middle pane (mw_places' hook: its TV entry,
        Back from the Guide, Search or a series' page): the pages over the
        list given way and the channels as they were left - or, from another
        place, TV's lists loaded. True: it shows."""
        if self.mode != "live":
            self.switch_mode("live")
        else:
            give_way = getattr(self, "_pages_give_way", None)
            if callable(give_way):
                give_way()
            else:
                self._close_guide_page()
                self._close_series_page()
                self._close_search_page()
        self._place_tv_sync()
        self.listw.setFocus(Qt.FocusReason.OtherFocusReason)
        entered = getattr(self, "_place_entered", None)
        if callable(entered):
            entered("tv")
        return True

    def _place_tv_refresh(self) -> None:
        """mw_places: a profile, a source, sync or a kids profile's
        categories changed what the place shows - its density (the
        profile's), its header and Recently watched shown again."""
        try:
            self._place_tv_sync()
        except Exception:
            log.exception("tv place: not shown again")

    def _place_tv_sync(self) -> None:
        """The place's pieces as the list now is: the header and its row
        over the live channels only; the strip's own size and sort controls
        give way to the header's (the size stays for a grid of tiles)."""
        top = self._place_tv_top
        if top is None:
            return
        on = self._place_tv_active()
        grid = self._grid_on()
        top.setVisible(on)
        # The places' shared header (mw_places) gives way to the place's own
        # while it shows - and comes back after, as it was.
        holder = getattr(self, "_place_header_holder", None)
        if holder is not None:
            if on and holder.isVisibleTo(self._list_page):
                holder.hide()
                self._place_tv_hid_header = True
            elif not on and self._place_tv_hid_header:
                holder.show()
                self._place_tv_hid_header = False
        sort_ctl = getattr(self, "_sort_ctl", None)
        size_ctl = getattr(self, "_size_ctl", None)
        if sort_ctl is not None:
            sort_ctl.setVisible(not on)
        if size_ctl is not None:
            size_ctl.setVisible(not on or grid)
        timer = self._place_tv_timer
        if timer is not None:
            if not on:
                timer.stop()
            elif not timer.isActive():
                timer.start()
        if not on:
            return
        want = self._place_tv_density_now()
        if want != self.delegate.tv_density:
            # Another profile's (switch_profile reads its lists again).
            self.delegate.set_tv_density(want)
            if not grid:
                self.listw.doItemsLayout()
        top.set_density(self.delegate.tv_density, shown=not grid)
        if not grid:
            self.listw.verticalScrollBar().setSingleStep(max(30, self.delegate.tv_row_h()))
        try:
            self._place_tv_header()
            self._place_tv_recent()
        except Exception:
            log.exception("tv place: the header or recently watched not shown")

    # -- the list's hooks (main_window) ------------------------------------------

    # Each only shows: a fault in one is logged and never reaches the list
    # being filled, the stream being started or the guide coming in.

    def _place_tv_list_shown(self) -> None:
        """_apply_filter: the list holds new rows."""
        try:
            if self.mode == "live":
                self._place_tv_learn(self.all_items)
            self._place_tv_sync()
        except Exception:
            log.exception("tv place: the header did not follow the list")

    def _place_tv_played(self) -> None:
        """_start_playback: History has the new title, the cards their mark."""
        try:
            if self._place_tv_top is not None and self._place_tv_active():
                self._place_tv_recent()
        except Exception:
            log.exception("tv place: recently watched did not follow a play")

    def _place_tv_guide_in(self) -> None:
        """A guide came in: what is on asked again."""
        try:
            self.delegate.forget_guide()
            if self._place_tv_top is not None and self._place_tv_active():
                self.listw.viewport().update()
                self._place_tv_top.recent.refresh()
        except Exception:
            log.exception("tv place: the guide's arrival not shown")

    def _place_tv_tick(self) -> None:
        if self._place_tv_top is None or not self._place_tv_top.isVisible():
            return
        self.listw.viewport().update()
        try:
            self._place_tv_recent()
        except Exception:
            log.exception("tv place: recently watched not brought up to date")

    def _place_tv_restyle(self) -> None:
        """A theme or a language: the header and the row drawn again."""
        if self._place_tv_top is None:
            return
        self._place_tv_top.restyle()
        self._place_tv_top.retranslate()
        if self._place_tv_active():
            self._place_tv_sync()

    # -- the header -----------------------------------------------------------------

    def _place_tv_title(self) -> str:
        item = self.cat_list.currentItem()
        text = item.text() if item is not None else ""
        head, sep, rest = text.partition("  ")
        if sep and head and not any(ch.isalnum() for ch in head):
            text = rest                     # an icon before the name
        code, label = category_flag(text)
        if code:
            text = label
        return text.strip() or tr("nav_tv")

    def _place_tv_header(self) -> None:
        top = self._place_tv_top
        if top is None:
            return
        top.set_title(self._place_tv_title())
        if self.cat_list.currentItem() is None:
            top.set_count("")               # its categories on their way
        else:
            model = self.list_model
            n = model.rowCount() if model.uniform else sum(
                1 for r in range(model.rowCount()) if not (model.item_at(r) or {}).get("_header"))
            order = _ORDER_WORDS.get(self._current_sort_order(), "tv_by_number")
            top.set_count(f"{self._count_text('live', n)} · {tr(order)}")
        box = self.sort_box
        top.set_sort([(box.itemData(i), box.itemText(i)) for i in range(box.count())],
                     self._current_sort_raw(), self._sort_button_words())

    def _place_tv_sort(self, key: str) -> None:
        """The header's sort: the list's own per-category order, through the
        control strip's path (its setting, the list sorted again)."""
        i = self.sort_box.findData(key)
        if i >= 0 and i != self.sort_box.currentIndex():
            self.sort_box.setCurrentIndex(i)

    def _place_tv_density(self, density: str) -> None:
        """Compact or airy, kept for the next start; the rows laid out again
        with the current one kept in view."""
        if density not in DENSITIES:
            return
        pick = getattr(self, "_place_view_pick", None)
        if callable(pick):
            pick("tv", density, DENSITY_KEY)    # not over the own while Just the list
        else:
            self.settings.setValue(DENSITY_KEY, density)
        self.delegate.set_tv_density(density)
        if self._place_tv_top is not None:
            self._place_tv_top.set_density(density, shown=not self._grid_on())
        if not self.delegate.tv_rows(self.list_model):
            return
        self.listw.doItemsLayout()
        self.listw.verticalScrollBar().setSingleStep(max(30, self.delegate.tv_row_h()))
        cur = self.listw.currentIndex()
        if cur.isValid():
            self.listw.scrollTo(cur, QAbstractItemView.ScrollHint.EnsureVisible)

    # -- Recently watched -------------------------------------------------------------

    def _place_tv_learn(self, rows) -> None:
        """This source's channels seen in its lists this session, by stream
        id: a card plays the channel as its list has it (its category, its
        catch-up, the logo it has now)."""
        if self._place_tv_known_of is not self.client or self._place_tv_known is None:
            self._place_tv_known, self._place_tv_known_of, self._place_tv_known_rows = {}, self.client, None
        if rows is self._place_tv_known_rows:
            return
        self._place_tv_known_rows = rows
        known = self._place_tv_known
        for it in rows or ():
            sid = it.get("stream_id")
            if sid is not None and not it.get("_header"):
                known[str(sid)] = it

    def _place_tv_recent_rows(self) -> list[dict]:
        """History's channels of this source as the lists may show them,
        newest first, at most RECENT_MAX."""
        if self._place_tv_known_of is not self.client:
            self._place_tv_known, self._place_tv_known_of, self._place_tv_known_rows = {}, self.client, None
        known = self._place_tv_known or {}
        excluded = self.overrides.excluded_ids(
            "live", include_locked=not self.parental.session_unlocked)
        hidden = self.channel_ov.anything_hidden()
        out: list[dict] = []
        seen: set[str] = set()
        for e in list(getattr(self.history, "entries", []) or []):
            if not isinstance(e, dict) or e.get("_kind") != "live":
                continue
            sid = e.get("stream_id")
            if sid is None:
                sid = e.get("_key")
            if sid is None or str(sid) in seen:
                continue
            seen.add(str(sid))
            row = known.get(str(sid))
            if row is None:
                row = {k: v for k, v in e.items() if not k.startswith("_")}
                row["stream_id"] = sid
            cid = row.get("category_id")
            if cid not in (None, "") and str(cid) in excluded:
                continue
            if hidden and self._channel_hidden(row, "live"):
                continue
            out.append(row)
            if len(out) >= RECENT_MAX * 2:
                break
        only = getattr(self, "_kids_only", None)
        if callable(only):
            out = only(out, "live")
        return out[:RECENT_MAX]

    def _place_tv_playing_key(self) -> str:
        """The channel of this source that plays, here or on a TV."""
        elsewhere = getattr(self, "_playing_elsewhere", None)
        if getattr(self, "_playing_group", None) == "live" and self._playing_key is not None:
            if not (callable(elsewhere) and elsewhere()):
                return str(self._playing_key)
        if getattr(self, "_cast_group", None) == "live" and getattr(self, "_cast_key", None) is not None:
            return str(self._cast_key)
        return ""

    def _place_tv_recent(self) -> None:
        top = self._place_tv_top
        if top is None:
            return
        shown = getattr(self, "_place_row_shown", None)
        if callable(shown) and not shown("tv_recent"):
            top.set_recent([])              # hidden, or Just the list
            return
        cards = []
        for row in self._place_tv_recent_rows():
            name = self.channel_display_name(row)
            nn = guide_now_next(self.xmltv, row) if self.xmltv.is_loaded() else None
            cards.append(Card(key=str(row.get("stream_id")), title=name,
                              line=nn.title if nn is not None else "",
                              image=str(row.get("stream_icon") or ""),
                              initials=initials_of(name, 3), data=row))
        top.set_recent(cards)
        top.set_playing(self._place_tv_playing_key())

    def _place_tv_playing_changed(self) -> None:
        if self._place_tv_top is not None:
            self._place_tv_top.set_playing(self._place_tv_playing_key())

    def _place_tv_card_line(self, card: Card) -> str:
        """A card's line as it is painted: what is on now."""
        row = card.data if isinstance(card.data, dict) else None
        x = getattr(self, "xmltv", None)
        if row is None or x is None or not x.is_loaded():
            return card.line
        nn = guide_now_next(x, row)
        return nn.title if nn is not None else ""

    def _place_tv_select(self, key) -> bool:
        """The row of *key* current in the list when the list holds it."""
        model = self.list_model
        for r in range(model.rowCount()):
            it = model.item_at(r)
            if it and not it.get("_header") and str(self._item_key(it)) == str(key):
                idx = model.index(r)
                self.listw.setCurrentIndex(idx)
                self.listw.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
                return True
        return False

    def _place_tv_play_card(self, card: Card) -> None:
        """A card played: as its row in the list when the list holds it (the
        list's own path), else as a channel played from the pane."""
        row = card.data if isinstance(card.data, dict) else None
        if row is None:
            return
        log.info("tv place: a recently watched channel played")
        if self._place_tv_select(row.get("stream_id")):
            self.play()
            return
        self._place_tv_play_row(row)

    @from_pane
    def _place_tv_play_row(self, row: dict) -> None:
        self.play_live_channel(row)

    def _place_tv_card_menu(self, card: Card, pos: QPoint) -> None:
        row = card.data if isinstance(card.data, dict) else None
        if row is None:
            return
        m = QMenu(self)
        m.addAction(tr("btn_play"), lambda: self._place_tv_play_card(card))
        m.addAction(tr("phone_page_show_in_list"),
                    lambda: self._reveal_channel_in_list(row))
        m.addSeparator()
        m.addAction(tr("tv_recent_remove"), lambda: self._place_tv_forget(row))
        m.exec(pos)
        m.deleteLater()

    def _place_tv_forget(self, row: dict) -> None:
        """A channel taken off Recently watched (out of History)."""
        sid = row.get("stream_id")
        for e in list(getattr(self.history, "entries", []) or []):
            if e.get("_kind") == "live" and str(e.get("stream_id", e.get("_key"))) == str(sid):
                self.history.remove(e.get("_key"), "live")
        self._place_tv_recent()
