"""Stream information in the window (core/stream_info.py keeps the values):
what the player observed for a live channel, remembered per source and
channel, and the technical line under its programme in the list. Only
while Playback's "Show stream information" switch is on, and never in
private viewing. The cache is a trace of what was watched: Clear recent
history drops it and backups leave it out."""

from __future__ import annotations

from typing import Any


class _StreamInfoMixin:
    _stream_info_cache: Any = None

    def _stream_info_store(self):
        from ..core.stream_info import StreamInfoCache
        if self._stream_info_cache is None:
            self._stream_info_cache = StreamInfoCache(self.settings)
        return self._stream_info_cache

    def _stream_info_forget(self) -> None:
        """Drop the cache held in memory (after Clear recent history)."""
        self._stream_info_cache = None

    def _remember_stream_info(self, key: str, info: dict) -> None:
        from ..core import privacy, stream_info
        if privacy.private() or not stream_info.enabled(self.settings):
            return
        if self._stream_info_store().put(key, info):
            self.listw.viewport().update()

    def channel_stream_info(self, item: dict, kind: str) -> str:
        from ..core import privacy, stream_info
        if privacy.private() or not stream_info.enabled(self.settings):
            return ""
        if kind not in ("live", "fav") and not (
                kind == "history" and item.get("_kind") == "live"):
            return ""
        return self._stream_info_store().get(self._health_key(self._item_key(item), item))
