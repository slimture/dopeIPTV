"""Category / item search mixin for MainWindow.

The sidebar 🔍 search: in the provider sections (TV/Movies/Series) it ranks
categories by how many of their channels match (a per-mode index fetched once);
in the folder/list sections (Favorites, Watch Later, Watched, Recordings,
History, Local files) it filters the on-screen items by name. Split out of
main_window.py; methods operate on MainWindow state (self.cat_search,
self.list_model, self._load_categories, ...) through the mixin, so behaviour
is identical. What each box says it does is set here too (#32): the one over
the list filters the list shown, the sidebar's finds categories.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from ..core.profiles import adult_category
from ..core.workers import run_async
from ..i18n import tr
from .mw_radio import SEARCH
from .widgets import plain_text


class _SearchMixin:
    """MainWindow mixin: the search box and debounced result filtering."""

    # What the sidebar's box finds in the sections with categories (#32):
    # a category by its name, or by a channel, film or series in it.
    _CAT_FIND = {"live": "cat_find_live", "vod": "cat_find_vod",
                 "series": "cat_find_series"}

    def _sync_search_hint(self) -> None:
        """The box over the list says what it looks through (#32): the list
        shown; in a folder of Local files that folder and its subfolders;
        under Radio's Search the whole radio-browser.info directory."""
        mode = getattr(self, "mode", "")
        if mode == "local" and self._local_walk_mode():
            key = "local_search_placeholder"
        elif mode == "radio" and getattr(self, "_current_cat", None) == SEARCH:
            key = "radio_search_placeholder"
        else:
            key = "list_filter_placeholder"
        self.search.setPlaceholderText(tr(key))

    def _sync_cat_search_hint(self) -> None:
        """The sidebar's box and its 🔍 toggle say what it finds: categories
        by name or by what is in them, or - in the list sections - the same
        filter of the list shown as the box over it."""
        text = tr(self._CAT_FIND.get(getattr(self, "mode", ""),
                                     "list_filter_placeholder"))
        self.cat_search.setPlaceholderText(text)
        self.cat_search_btn.setToolTip(text)

    def _toggle_cat_search(self, on: bool) -> None:
        self.cat_search.setVisible(on)
        if on:
            self.cat_search.setFocus()
        elif self.cat_search.text():
            self.cat_search.clear()   # clearing restores the category list

    def _on_cat_search(self, _text: str) -> None:
        # Debounce so a fast typist doesn't trigger a rebuild per keystroke.
        self._cat_search_timer.start()

    def _run_category_search(self) -> None:
        q = self.cat_search.text().strip().lower()
        if self.mode in ("live", "vod", "series"):
            if not q:
                self._load_categories()   # restore the normal category list
                return
            self._ensure_search_index(
                self.mode, lambda idx: self._render_cat_search(q, idx))
        elif self.mode in ("fav", "watchlist", "watched", "rec", "history",
                           "local"):
            # Folder/list sections have no provider categories to rank, so the
            # search just filters the items on screen by name.
            if not q:
                self._apply_filter()
            else:
                self._render_item_search(q)

    def _render_item_search(self, q: str) -> None:
        kind = self._content_kind()
        text = self.search.text().lower().strip()   # honour the top search too
        if self.mode == "local" and self._local_walk_mode():
            # A folder's rows are the top search's matches already - found
            # by their file names, which the name filter could drop.
            text = ""
        hidden = self.channel_ov.anything_hidden()
        items = [it for it in (self.all_items or [])
                 if not it.get("_header")
                 and not (hidden and self._channel_hidden(it, kind))]
        kids = getattr(self, "_kids_keep", lambda _m: None)(kind)
        if kids is not None:
            items = [it for it in items if kids(it)]
        name = self._display_name_fn()
        if text:
            items = [it for it in items if text in name(it).lower()]
        items = [it for it in items if q in name(it).lower()]
        self.list_model.set_items(self._sorted(items), kind)
        self._set_list_status(self._count_text(kind, len(items)))

    def _ensure_search_index(self, mode: str, cb) -> None:
        """Fetch every channel of *mode* once (grouped by category_id) so the
        search can rank categories by how many of their channels match. Cached
        per mode for the session; rebuilt when categories reload."""
        cached = self._search_index_cache.get(mode)
        if cached is not None:
            cb(cached)
            return
        fetch = {"live": self.client.live_streams,
                 "vod": self.client.vod_streams,
                 "series": self.client.series_list}[mode]
        # A kids profile: no title for adults among the samples either.
        kids = getattr(self, "_kids_keep", lambda _m: None)(mode)

        def build() -> dict:
            # On the worker, names lowered once: done on the GUI thread, and
            # every keystroke lowering them all again, a big catalogue held
            # the window up each time.
            from collections import defaultdict
            idx: dict = defaultdict(list)
            for it in fetch(None) or []:
                cid = it.get("category_id")
                if cid is None or (kids is not None and not kids(it)):
                    continue
                n = it.get("name") or it.get("title") or ""
                idx[cid].append((n, n.lower()))
            return idx

        def done(idx) -> None:
            self._search_index_cache[mode] = idx
            cb(idx)

        run_async(self.pool, build, done, lambda _m: cb({}))

    def _render_cat_search(self, q: str, idx: dict) -> None:
        results = []
        # A kids profile: no category named for adults, and no locked one
        # (as the category list leaves them out, main_window). Anyone else:
        # a locked one while the lock is shut by its name alone, marked as
        # the list marks it - nothing of what is in it.
        kids = getattr(self, "_profile_kids", lambda: False)()
        for c in getattr(self, "_raw_categories", []):
            cid = c.get("category_id")
            if self.overrides.is_hidden(self.mode, cid):
                continue
            cname = self.overrides.display_name(
                self.mode, cid, c.get("category_name", "?"))
            locked = (self.overrides.is_locked(self.mode, cid)
                      and not self.parental.session_unlocked)
            if kids and (adult_category(cname, c.get("category_name")) or locked):
                continue
            name_match = q in cname.lower()
            if locked:
                if name_match:
                    results.append((1_000_000, cname + "  " + tr("cat_locked_suffix"), cid, []))
                continue
            chans = idx.get(cid, [])
            matches = [n for n, low in chans if q in low]
            if not name_match and not matches:
                continue
            # Rank: a category whose NAME matches wins; among the rest, the more
            # of its channels match the higher it sits (so "bbc" surfaces UK,
            # with its many BBC channels, above a country with just one).
            score = (1_000_000 if name_match else 0) + len(matches)
            sample = (matches or [n for n, _low in chans[:3]])[:3]
            results.append((score, cname, cid, sample))
        results.sort(key=lambda r: (-r[0], r[1].lower()))

        self.cat_list.blockSignals(True)
        self.cat_list.clear()
        for _score, cname, cid, sample in results:
            label = cname
            if sample:
                label += "  ·  " + ", ".join(sample)
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, cid)
            it.setToolTip(plain_text(cname))     # the provider's category name
            self.cat_list.addItem(it)
        if not results:
            none_it = QListWidgetItem(tr("cat_search_none"))
            none_it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.cat_list.addItem(none_it)
        self.cat_list.blockSignals(False)
