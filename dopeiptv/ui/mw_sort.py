"""Sort-order mixin for MainWindow.

The list sort controls: name / recently-added keys, the per-mode sort setting,
the sort combo sync, and the _sorted() that orders a list of items. Pure logic
moved out of main_window.py; behaviour is identical.
"""
from __future__ import annotations

from datetime import datetime

from ..i18n import tr

#: The orders a list can be in, as their words name them (the sort button
#: and its menu, every place alike).
ORDER_WORDS = {"default": "sort_provider", "alpha_asc": "sort_az",
               "alpha_desc": "sort_za", "recent": "option_sort_recent"}


def order_words(order: str, updated: bool = False) -> str:
    """An order's words; *updated* for series, whose newest are the
    recently updated ones."""
    if order == "recent" and updated:
        return tr("place_sort_updated")
    return tr(ORDER_WORDS.get(order, "sort_provider"))


class _SortMixin:
    """MainWindow mixin: per-category and global list sort order."""
    @staticmethod
    def _sort_key_name(it):
        return (it.get("name") or it.get("title") or "").lower()

    def _sort_setting_key(self) -> str:
        """Per-category sort key, so each category can keep its own order."""
        return f"sort::{self.mode}::{getattr(self, '_current_cat', None)!r}"

    def _current_sort_raw(self) -> str:
        """The current category's own choice: 'global' (follow the app-wide
        default) unless it has been overridden."""
        return self.settings.value(self._sort_setting_key(), "global")

    def _sort_options(self) -> list[tuple[str, str]]:
        """The sort menu: first what every category follows - named with
        the order that is - then each order a category can have of its own."""
        series = getattr(self, "mode", "") == "series"
        return ([("global", tr("sort_as_all", order=order_words(self._global_sort_order(), series)))]
                + [(k, order_words(k)) for k in ("default", "alpha_asc", "alpha_desc", "recent")])

    def _sync_sort_box(self) -> None:
        """Point the toolbar sort dropdown at the current category's order, so
        it never carries a stale value into _inline_view_changed (which would
        write that order onto whatever category is showing now) - and its
        first entry at the order every category follows now."""
        if not hasattr(self, "sort_box"):
            return
        self.sort_box.blockSignals(True)
        for i, (key, words) in enumerate(self._sort_options()):
            if i < self.sort_box.count() and self.sort_box.itemData(i) == key:
                self.sort_box.setItemText(i, words)
        i = self.sort_box.findData(self._current_sort_raw())
        if i >= 0:
            self.sort_box.setCurrentIndex(i)
        self.sort_box.blockSignals(False)

    def _global_sort_order(self) -> str:
        """The order a category that has none of its own is in: Settings'
        - the Series place recently updated first while none was chosen
        (where the source says when - rows that say nothing keep theirs)."""
        if (getattr(self, "mode", "") == "series" and not getattr(self, "series_ctx", None)
                and not self.settings.contains("sort_order")):
            return "recent"
        order = str(self.settings.value("sort_order", "default") or "default")
        return order if order in ORDER_WORDS else "default"

    def _current_sort_order(self) -> str:
        """The effective sort order for the current category - its own
        override, or the global default when it is set to follow global."""
        raw = self._current_sort_raw()
        if raw == "global":
            return self._global_sort_order()
        return raw

    def _sort_button_words(self, updated: bool = False) -> str:
        """What the sort button says: the order the list is in now."""
        return order_words(self._current_sort_order(), updated)

    @staticmethod
    def _recency_key(it) -> int:
        # Newest-first key that works across views: provider "added" (a
        # series' "last_modified": when it was last updated), else the
        # history "_watched_at" ISO timestamp.
        for field in ("added", "last_modified"):
            a = it.get(field)
            if a:
                try:
                    return int(a)
                except (TypeError, ValueError):
                    pass
        wa = it.get("_watched_at")
        if wa:
            try:
                return int(datetime.fromisoformat(wa).timestamp())
            except (TypeError, ValueError):
                pass
        return 0

    def _sorted(self, items: list) -> list:
        order = self._current_sort_order()
        if order == "alpha_asc":
            return sorted(items, key=self._sort_key_name)
        if order == "alpha_desc":
            return sorted(items, key=self._sort_key_name, reverse=True)
        if order == "recent":
            return sorted(items, key=self._recency_key, reverse=True)
        return items
