"""Sort-order mixin for MainWindow.

The list sort controls: name / recently-added keys, the per-mode sort setting,
the sort combo sync, and the _sorted() that orders a list of items. Pure logic
moved out of main_window.py; behaviour is identical.
"""
from __future__ import annotations

from datetime import datetime


class _SortMixin:
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

    def _sync_sort_box(self) -> None:
        """Point the toolbar sort dropdown at the current category's order, so
        it never carries a stale value into _inline_view_changed (which would
        write that order onto whatever category is showing now)."""
        if not hasattr(self, "sort_box"):
            return
        self.sort_box.blockSignals(True)
        i = self.sort_box.findData(self._current_sort_raw())
        if i >= 0:
            self.sort_box.setCurrentIndex(i)
        self.sort_box.blockSignals(False)

    def _current_sort_order(self) -> str:
        """The effective sort order for the current category - its own
        override, or the global default when it is set to follow global."""
        raw = self._current_sort_raw()
        if raw == "global":
            return self.settings.value("sort_order", "default")
        return raw

    @staticmethod
    def _recency_key(it) -> int:
        # Newest-first key that works across views: provider "added", else the
        # history "_watched_at" ISO timestamp.
        a = it.get("added")
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
