"""Category / item search mixin for MainWindow.

The sidebar 🔍 search: in the provider sections (TV/Movies/Series) it ranks
categories by how many of their channels match (a per-mode index fetched once);
in the folder/list sections (Favorites, Watch Later, Watched, Recordings,
History) it filters the on-screen items by name. Split out of main_window.py;
methods operate on MainWindow state (self.cat_search, self.list_model,
self._load_categories, ...) through the mixin, so behaviour is identical.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from ..core.workers import run_async
from ..i18n import tr


class _SearchMixin:
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
        elif self.mode in ("fav", "watchlist", "watched", "rec", "history"):
            # Folder/list sections have no provider categories to rank, so the
            # search just filters the items on screen by name.
            if not q:
                self._apply_filter()
            else:
                self._render_item_search(q)

    def _render_item_search(self, q: str) -> None:
        kind = self._content_kind()
        text = self.search.text().lower().strip()   # honour the top search too
        items = [it for it in (self.all_items or [])
                 if not it.get("_header")
                 and not self._channel_hidden(it, kind)]
        if text:
            items = [it for it in items
                     if text in self.channel_display_name(it).lower()]
        items = [it for it in items
                 if q in self.channel_display_name(it).lower()]
        self.list_model.set_items(self._sorted(items), kind)
        self._set_status(f"{len(items)} {self.LABELS.get(kind, '')}")

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

        def done(items) -> None:
            from collections import defaultdict
            idx: dict = defaultdict(list)
            for it in items or []:
                cid = it.get("category_id")
                if cid is None:
                    continue
                idx[cid].append(it.get("name") or it.get("title") or "")
            self._search_index_cache[mode] = idx
            cb(idx)

        run_async(self.pool, fetch, done, lambda _m: cb({}))

    def _render_cat_search(self, q: str, idx: dict) -> None:
        results = []
        for c in getattr(self, "_raw_categories", []):
            cid = c.get("category_id")
            if self.overrides.is_hidden(self.mode, cid):
                continue
            cname = self.overrides.display_name(
                self.mode, cid, c.get("category_name", "?"))
            name_match = q in cname.lower()
            chans = idx.get(cid, [])
            matches = [n for n in chans if q in n.lower()]
            if not name_match and not matches:
                continue
            # Rank: a category whose NAME matches wins; among the rest, the more
            # of its channels match the higher it sits (so "bbc" surfaces UK,
            # with its many BBC channels, above a country with just one).
            score = (1_000_000 if name_match else 0) + len(matches)
            sample = (matches or chans)[:3]
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
            it.setToolTip(cname)
            self.cat_list.addItem(it)
        if not results:
            none_it = QListWidgetItem(tr("cat_search_none"))
            none_it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.cat_list.addItem(none_it)
        self.cat_list.blockSignals(False)
