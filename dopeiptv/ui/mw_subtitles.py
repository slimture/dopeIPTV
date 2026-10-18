"""MainWindow mixin: subtitles from OpenSubtitles, and keeping them.

"Find subtitles online…" in the player's Subtitles menu opens the search
(ui/subtitle_search.py) for what is playing. A subtitle loaded for a title -
fetched there or picked from disk - comes back with that title the next
time it plays, so a resumed film does not cost another download.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..core.log import log
from ..i18n import current_language
from ..providers.metadata import PosterResolver
from ..providers.opensubtitles import OpenSubtitles, bundled_key, search_language

# Titles whose subtitle is remembered; the oldest is forgotten first.
_REMEMBER_MAX = 300
_SETTINGS_KEY = "subtitle_files"


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _SubtitlesMixin:
    """MainWindow mixin: the OpenSubtitles search and remembered subtitles."""

    def _subtitle_client(self) -> OpenSubtitles | None:
        """The client for the key in Settings (or the built-in one), kept
        while the key and account stay the same so a sign-in is reused."""
        key = ((self.settings.value("os_api_key", "") or "").strip()
               or bundled_key())
        if not key:
            return None
        user = self.settings.value("os_username", "") or ""
        password = self.settings.value("os_password", "") or ""
        sig = (key, user, password)
        client = getattr(self, "_os_client", None)
        if client is None or getattr(self, "_os_sig", None) != sig:
            client = OpenSubtitles(key, user, password)
            self._os_client, self._os_sig = client, sig
        return client

    def _subtitle_ctx(self) -> dict[str, Any] | None:
        """What the search knows about what is playing: words to search for,
        the ids that pin the title down, and a local file to hash."""
        lp = getattr(self, "_last_playback", None) or {}
        kind = lp.get("kind")
        if kind not in ("movie", "episode", "local", "recording"):
            return None
        item = lp.get("item") or {}
        raw = item.get("name") or item.get("title") or lp.get("title") or ""
        ctx: dict[str, Any] = {"query": "", "tmdb_id": None,
                               "parent_tmdb_id": None, "season": None,
                               "episode": None, "path": None}
        if kind == "movie":
            ctx["query"] = PosterResolver.clean_title(raw)[0] or raw
            ctx["tmdb_id"] = self._tmdb_id_for_item(item, "vod")
        elif kind == "episode":
            sctx = lp.get("series_ctx") or {}
            show = sctx.get("name") or sctx.get("title") or ""
            show = PosterResolver.clean_title(show)[0] or show
            season = _int(item.get("season"))
            number = _int(item.get("episode_num"))
            ctx["season"], ctx["episode"] = season, number
            tmdb = getattr(self, "tmdb", None)
            if tmdb is not None and show:
                ctx["parent_tmdb_id"] = tmdb.tmdb_id_for(show, "series")
            ctx["query"] = (f"{show} S{season:02d}E{number:02d}"
                            if show and season is not None
                            and number is not None else show or raw)
        else:
            path = item.get("_path") or lp.get("url") or ""
            ctx["path"] = path if path and os.path.isfile(path) else None
            ctx["query"] = item.get("_clean_title") or raw
        return ctx

    def _open_subtitle_search(self) -> None:
        ctx = self._subtitle_ctx()
        if ctx is None or self.player is None:
            return
        from .subtitle_search import SubtitleSearchDialog
        lang = search_language(self.settings.value("sub_lang", "") or "",
                               current_language())
        SubtitleSearchDialog(self, ctx, lang).exec()

    # -- remembering ------------------------------------------------------------

    def _subtitle_memory(self) -> dict[str, str]:
        try:
            data = json.loads(self.settings.value(_SETTINGS_KEY, "") or "{}")
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _on_subtitle_added(self, path: str) -> None:
        """A subtitle is showing: file it under the title that is playing."""
        lp = getattr(self, "_last_playback", None) or {}
        kind, key = lp.get("kind"), lp.get("key")
        if kind not in self._RESUMABLE or key is None or not path:
            return
        memory = self._subtitle_memory()
        memory.pop(f"{kind}:{key}", None)
        memory[f"{kind}:{key}"] = path      # re-insert = newest
        while len(memory) > _REMEMBER_MAX:
            memory.pop(next(iter(memory)))
        self.settings.setValue(_SETTINGS_KEY, json.dumps(memory))
        log.info("subtitles: remembered %s for %s:%s", path, kind, key)

    def _remembered_subtitles(self, kind: str, key: Any) -> list[str]:
        path = self._subtitle_memory().get(f"{kind}:{key}")
        return [path] if path and os.path.isfile(path) else []
