"""The Guide page: the programme guide (ui/epg_grid.py) in the middle pane,
where the lists are, with the sidebar and the player beside it - the Guide
is one of the places. Pop out puts the same guide in a window of its own.

What it covers is settled as it opens (_guide_scope): the TV list's
category, the Favorites folder shown, else the favourite channels, else
the whole line-up (fetched on a worker). Over TV it follows the list: a
category picked or arrowed to in the sidebar while it shows puts that
category's channels in it, and it stays (_guide_follow_list, as the list's
rows come in) - a folder of favourite channels the same. A category
double-clicked, or Show in the guide in its menu, opens the guide on it
(_guide_for_category). The guide is kept while the app
runs and shown again as it was - same source, same channels, the same day
- and drawn afresh otherwise. Showing or leaving it never touches the
player.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from .epg_grid import EpgGrid, EpgGridDialog
from .theme import P


def _lineup_sig(channels: list) -> tuple:
    """The channels a guide covers, as much as tells two line-ups apart."""
    return tuple((c.get("stream_id"), c.get("name")) for c in channels)


class GuidePage(QWidget):
    """The middle pane's Guide page: the guide, or a word while the line-up
    it covers is still on its way."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("GuidePage")
        lay = QVBoxLayout(self)
        # The list page's own margins, so the two pages line up.
        lay.setContentsMargins(0, 14, 0, 10)
        lay.setSpacing(0)
        self._lay = lay
        self.note = QLabel("")
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note.setWordWrap(True)
        self.note.hide()
        lay.addWidget(self.note, 1)
        self.grid: EpgGrid | None = None
        self.restyle()

    def restyle(self) -> None:
        self.note.setStyleSheet(f"color:{P['muted']}; font-size:13px;")

    def put(self, grid: EpgGrid) -> None:
        """Show *grid* here (it becomes visible with the page)."""
        if self.grid is not None and self.grid is not grid:
            self.take(self.grid)
        self.grid = grid
        if self._lay.indexOf(grid) < 0:
            self._lay.addWidget(grid, 1)
        grid.set_in_window(False)
        grid.show()
        self.note.hide()

    def take(self, grid: EpgGrid) -> None:
        """Let go of *grid* (popped out, or dropped)."""
        if self._lay.indexOf(grid) >= 0:
            self._lay.removeWidget(grid)
        if self.grid is grid:
            self.grid = None

    def say(self, text: str) -> None:
        """A word in the guide's place: loading, or why there is none."""
        if self.grid is not None:
            self.grid.hide()
        self.note.setText(text)
        self.note.show()


class _GuideMixin:
    """MainWindow mixin: the Guide page in the middle pane, and its pop-out."""

    _guide_page: GuidePage | None = None
    _guide_grid: EpgGrid | None = None
    _guide_window: EpgGridDialog | None = None
    _guide_gen = 0

    # -- where the guide is ------------------------------------------------------

    def _guide_page_showing(self) -> bool:
        """The Guide is the middle pane's page (seen or not: the pane can be
        folded away for fullscreen or focus mode)."""
        page = self._guide_page
        stack = getattr(self, "_mid_stack", None)
        return (page is not None and stack is not None
                and stack.currentWidget() is page)

    def _guide_page_visible(self) -> bool:
        return self._guide_page_showing() and self._guide_page.isVisible()

    def _ensure_guide_page(self) -> GuidePage:
        if self._guide_page is None:
            self._guide_page = GuidePage()
            self._mid_stack.addWidget(self._guide_page)
        return self._guide_page

    def _close_guide_page(self) -> None:
        """Put the list back in the middle pane. The guide is kept, hidden
        (its timers stop with it), for the next time."""
        if self._guide_page_showing():
            self._mid_stack.setCurrentIndex(0)

    def _leave_guide_page(self) -> None:
        """Back where you were: the place you came from, as it was left,
        with its entry lit again (mw_places)."""
        if not self._guide_page_showing():
            return
        self._close_guide_page()
        self._return_to_place()

    def _guide_category_clicked(self, _item=None) -> None:
        """A category picked in the sidebar while the Guide shows: over TV
        the guide follows it - its channels come in as the list's do - and
        stays; elsewhere its list, the way a nav entry leads to its own."""
        if not self._guide_page_showing():
            return
        if self._guide_covers(self.cat_list.currentItem()):
            self._guide_follow_list()
            return
        self._close_guide_page()
        self._place_entered(self._list_place)

    def _guide_covers(self, item) -> bool:
        """Whether *item* of the sidebar is channels a guide can show: a TV
        category, or a folder of favourite channels (or all of them) -
        never films or series."""
        if item is None or self.series_ctx:
            return False
        data = item.data(Qt.ItemDataRole.UserRole)
        if self.mode == "live":
            return True
        if self.mode == "fav":
            return data is None or (isinstance(data, tuple) and data[0] in ("chan", "all"))
        return False

    def _guide_for_category(self, item=None) -> None:
        """A category double-clicked, or Show in the guide in its menu: that
        category picked and the guide opened on it. Its channels may still
        be on their way: the guide takes them when they come
        (_guide_follow_list). A guide popped out is brought forward as it
        is."""
        if item is None or not self._guide_covers(item):
            return
        if self.cat_list.currentItem() is not item:
            self.cat_list.setCurrentItem(item)
        self._open_epg_guide()

    def _guide_follow_list(self) -> None:
        """The Guide page over TV covers the list's category: called each
        time the list's rows are put in (_apply_filter), so a category
        picked, or arrowed to, while the guide shows puts its channels in
        it. The same channels again change nothing (a search typed, a
        refresh); the focus stays in the sidebar while it is there."""
        if not self.all_items or not self._guide_page_showing() \
                or not self._guide_covers(self.cat_list.currentItem()):
            return
        key, title, channels = self._guide_scope(False)
        grid = self._guide_grid
        if grid is not None and grid.scope_key == key and self._guide_page.grid is grid:
            return
        self._guide_gen += 1
        grid = self._new_guide_grid(key, title, channels)
        self._guide_page.put(grid)
        if not self.cat_list.hasFocus():
            grid.view.setFocus()
        log.info("guide: follows the list's category (%d channels)", len(channels))

    def _relight_nav(self) -> None:
        """Light the entry of what the window shows (a click lit the Guide
        entry, but the guide came up in its window instead)."""
        if self._guide_page_showing():
            self._check_nav("guide")
        elif self._search_page_showing():
            self._check_nav("search")
        else:
            self._check_nav(self._nav_key_for_mode(self.mode))

    # -- opening it ---------------------------------------------------------------

    def _open_epg_guide(self, global_scope: bool = False) -> None:
        """Open the TV guide: its page in the middle pane - or, popped out,
        its window brought forward. Scoped to the current list in TV /
        Favorites mode; *global_scope* skips those (the whole line-up, or
        the favourite channels)."""
        self._ensure_xmltv_loaded()
        if self._guide_window is not None:
            self._raise_guide_window()
            self._relight_nav()
            return
        key, title, channels = self._guide_scope(global_scope)
        if getattr(self, "_player_fs", False):
            # The video fills the screen and the pane is folded away: the
            # guide comes over it in its window, as it always did.
            self._close_guide_page()
            self._relight_nav()
            self._guide_in_window(key, title, channels)
            return
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        page = self._ensure_guide_page()
        grid = self._kept_guide(key)
        self._guide_gen += 1
        if grid is None and channels is not None:
            grid = self._new_guide_grid(key, title, channels)
        if grid is not None:
            page.put(grid)
        else:
            page.say(tr("status_loading_channels"))
            self._fetch_guide_lineup(self._guide_gen, key, title,
                                     self._guide_lineup_for_page)
        self._mid_stack.setCurrentWidget(page)
        self._place_entered("guide")
        if grid is not None:
            grid.view.setFocus()

    def _guide_scope(self, global_scope: bool) -> tuple:
        """(key, title, channels) of the guide to open now. *channels* is
        None for the whole line-up, which is fetched (_fetch_guide_lineup).
        The key tells a guide kept from an earlier visit apart: the source,
        the scope and its channels."""
        src = id(self.client)
        # A kids profile: no channel for adults in any of them (mw_profiles).
        kids = getattr(self, "_kids_only", lambda rows, _m="live": list(rows or []))
        if not global_scope and self.mode == "live" and self.all_items:
            cat = self.cat_list.currentItem()
            title = cat.text() if cat else "All"
            chans = kids(self.all_items)
            return ("live", src, title, _lineup_sig(chans)), title, chans
        # In Favorites, scope the guide to the favorite CHANNELS shown (a
        # folder, or all of them) - movies/series have no EPG. Other Trakt/
        # media sections simply have no live channels to guide.
        if not global_scope and self.mode == "fav":
            chans = kids(self._favorite_channels_for_guide())
            cat = self.cat_list.currentItem()
            title = cat.text().strip() if cat else tr("nav_favorites")
            return ("fav", src, title, _lineup_sig(chans)), title, chans
        # No meaningful live scope (Mine's other lists, Movies, Series...). The guide
        # used to cover an arbitrary slice here: an auto-selected first
        # category, or the first 300 of the provider's full dump. Favorite
        # channels are the lineup the user actually curated - guide those
        # when any exist; the full lineup stays the fallback.
        try:
            exclude = (() if self.parental.session_unlocked
                       else self.favs.locked_groups())
            favs = kids(self.favs.items(None, exclude_groups=exclude))
        except Exception:
            favs = []
        if favs:
            title = tr("nav_favorites")
            return ("favs", src, _lineup_sig(favs)), title, favs
        return ("all", src, bool(self.parental.session_unlocked),
                getattr(self, "_profile_kids", lambda: False)()), None, None

    def _kept_guide(self, key) -> EpgGrid | None:
        """The guide from an earlier visit, when it covers the same and is
        still fresh - shown again as it was, nothing drawn anew."""
        grid = self._guide_grid
        if grid is None:
            return None
        if grid.scope_key == key and not grid.stale():
            return grid
        self._drop_guide_grid()
        return None

    def _new_guide_grid(self, key, title, channels) -> EpgGrid:
        self._drop_guide_grid()
        grid = EpgGrid(self, channels, title)
        grid.scope_key = key
        grid.pop_out_requested.connect(self._pop_out_guide)
        grid.played.connect(self._guide_followed_play)
        self._guide_grid = grid
        return grid

    def _drop_guide_grid(self) -> None:
        grid = self._guide_grid
        self._guide_grid = None
        if grid is None:
            return
        if self._guide_page is not None:
            self._guide_page.take(grid)
        grid.hide()
        grid.deleteLater()

    def _fetch_guide_lineup(self, gen: int, key, title, then) -> None:
        """The whole line-up, on a worker (tens of thousands of channels
        are not gone through on the GUI thread), then *then*(gen, key,
        title, channels) - unless another guide was asked for meanwhile."""
        keep = self._catalog_filter("live")
        client = self.client

        def work():
            return [c for c in client.live_streams(None) or [] if keep(c)]

        def done(channels, gen=gen, key=key, title=title):
            if gen == self._guide_gen:
                then(gen, key, title, channels)

        def fail(err, gen=gen):
            log.warning("guide: the line-up did not load: %s", err)
            if gen == self._guide_gen and self._guide_page is not None:
                self._guide_page.say(tr("detail_epg_failed"))

        run_async(self.pool, work, done, fail)

    def _guide_lineup_for_page(self, gen, key, title, channels) -> None:
        if not self._guide_page_showing():
            return
        grid = self._new_guide_grid(key, title, channels)
        self._guide_page.put(grid)
        grid.view.setFocus()

    def _guide_followed_play(self) -> None:
        """A channel tuned from the guide on the page: mark it there now."""
        if self._guide_page_showing() and self._guide_grid is not None:
            self._guide_grid.refresh_playing()

    # -- its window -------------------------------------------------------------

    def _pop_out_guide(self) -> None:
        """The same guide - where it was scrolled, what was picked - in a
        window of its own; the page goes back to where you were."""
        grid = self._guide_grid
        if grid is None or self._guide_window is not None:
            return
        if self._guide_page is not None:
            self._guide_page.take(grid)
        self._leave_guide_page()
        self._open_guide_window(grid)

    def _guide_in_window(self, key, title, channels) -> None:
        grid = self._kept_guide(key)
        self._guide_gen += 1
        if grid is None and channels is None:
            self._fetch_guide_lineup(self._guide_gen, key, title,
                                     self._guide_lineup_for_window)
            return
        if grid is None:
            grid = self._new_guide_grid(key, title, channels)
        elif self._guide_page is not None:
            self._guide_page.take(grid)
        self._open_guide_window(grid)

    def _guide_lineup_for_window(self, gen, key, title, channels) -> None:
        if self._guide_window is None:
            self._open_guide_window(self._new_guide_grid(key, title, channels))

    def _open_guide_window(self, grid: EpgGrid) -> None:
        # Not modal: the window beside it stays usable, and the Guide entry
        # or its key bring this one forward again.
        dlg = EpgGridDialog(self, grid=grid)
        dlg.finished.connect(self._on_guide_window_closed)
        self._guide_window = dlg
        dlg.show()
        self._raise_guide_window()

    def _raise_guide_window(self) -> None:
        dlg = self._guide_window
        if dlg is None:
            return
        if dlg.isMinimized():
            dlg.showNormal()
        dlg.raise_()
        dlg.activateWindow()

    def _on_guide_window_closed(self, _result: int = 0) -> None:
        """The window closed: the guide goes back to the page, kept for the
        next time (unless a newer one took its place meanwhile)."""
        dlg = self._guide_window
        self._guide_window = None
        if dlg is None:
            return
        grid = dlg.release_grid()
        if grid is self._guide_grid:
            self._ensure_guide_page().put(grid)
        else:
            grid.deleteLater()
        dlg.deleteLater()

    def _guide_source_changed(self) -> None:
        """Another source is in use: its channels are not the guide's. A
        popped-out window closes, the page gives way to the list (which
        loads the new source) and the kept guide is let go."""
        if self._guide_window is not None:
            self._guide_window.close()
        if self._guide_page_showing():
            self._close_guide_page()
            self._check_nav(self._nav_key_for_mode(self.mode))
        self._drop_guide_grid()

    # -- theme and language -------------------------------------------------------

    def _guide_restyle(self) -> None:
        """A new theme or language: the guide on the page is drawn again in
        it, where it was; one kept off screen is let go (drawn new next
        time). A popped-out window keeps its look until it closes."""
        if self._guide_page is not None:
            self._guide_page.restyle()
        old = self._guide_grid
        if old is None or self._guide_window is not None:
            return
        if not self._guide_page_showing():
            self._drop_guide_grid()
            return
        # The time at the left edge, not the scroll value: the new board
        # works its scale out afresh as it comes on screen.
        left = old.left_time()
        v = old.view.verticalScrollBar().value()
        grid = self._new_guide_grid(old.scope_key, old.category_name,
                                    old._all_channels)
        self._guide_page.put(grid)
        grid.scroll_to_time(left)
        grid.view.verticalScrollBar().setValue(v)
