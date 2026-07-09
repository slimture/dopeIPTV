"""Data stores: favorites, history, parental control, and per-playlist overrides.

All stores persist their state via QSettings (JSON-serialized) and are
scoped per-playlist so each provider keeps its own data.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QSettings


class FavoriteStore:
    """Favorite channels in user-defined groups, persisted via QSettings."""

    def __init__(self, settings: QSettings, key: str = "favorites") -> None:
        self.settings = settings
        self.key = key
        try:
            self.groups: dict[str, list[dict]] = json.loads(
                settings.value(key, "") or "{}")
        except Exception:
            self.groups = {}
        if not isinstance(self.groups, dict):
            self.groups = {}

    def _save(self) -> None:
        self.settings.setValue(self.key, json.dumps(self.groups))

    def group_names(self) -> list[str]:
        return sorted(self.groups, key=str.lower)

    def add(self, group: str, item: dict) -> None:
        items = self.groups.setdefault(group, [])
        stream_id = item.get("stream_id")
        if not any(x.get("stream_id") == stream_id for x in items):
            items.append(item)
        self._save()

    def remove(self, stream_id: Any, group: str | None = None) -> None:
        for g in ([group] if group else list(self.groups)):
            self.groups[g] = [x for x in self.groups.get(g, [])
                              if x.get("stream_id") != stream_id]
        self._save()

    def is_favorite(self, stream_id) -> bool:
        for items in self.groups.values():
            if any(x.get("stream_id") == stream_id for x in items):
                return True
        return False

    def groups_for(self, stream_id) -> list[str]:
        return [g for g, items in self.groups.items()
                if any(x.get("stream_id") == stream_id for x in items)]

    def remove_group(self, group: str) -> None:
        self.groups.pop(group, None)
        if group in self.locked_groups():
            self.set_group_locked(group, False)
        self._save()

    def locked_groups(self) -> set[str]:
        try:
            locked = json.loads(
                self.settings.value(f"{self.key}_locked", "") or "[]")
        except Exception:
            locked = []
        return set(locked) if isinstance(locked, list) else set()

    def set_group_locked(self, group: str, locked: bool) -> None:
        current = self.locked_groups()
        (current.add if locked else current.discard)(group)
        self.settings.setValue(f"{self.key}_locked",
                               json.dumps(sorted(current)))

    def is_locked(self, group: str) -> bool:
        return group in self.locked_groups()

    def items(self, group: str | None = None,
              exclude_groups: tuple[str, ...] = ()) -> list[dict]:
        if group:
            return list(self.groups.get(group, []))
        result: list[dict] = []
        seen: set = set()
        for g in self.group_names():
            if g in exclude_groups:
                continue
            for it in self.groups[g]:
                stream_id = it.get("stream_id")
                if stream_id not in seen:
                    seen.add(stream_id)
                    result.append(it)
        return result


class HistoryStore:
    """Recently played items, persisted via QSettings."""

    MAX_ENTRIES: int = 300

    def __init__(self, settings: QSettings, key: str = "history") -> None:
        self.settings = settings
        self.key = key
        try:
            self.entries: list[dict] = json.loads(
                settings.value(key, "") or "[]")
        except Exception:
            self.entries = []
        if not isinstance(self.entries, list):
            self.entries = []

    def _save(self) -> None:
        self.settings.setValue(self.key,
                               json.dumps(self.entries[:self.MAX_ENTRIES]))

    def add(self, url: str, title: str, icon_url: str | None,
            key: str, kind: str) -> None:
        if not url:
            return
        self.entries = [e for e in self.entries
                        if not (e.get("_key") == key and e.get("_kind") == kind)]
        self.entries.insert(0, {
            "name": title, "stream_icon": icon_url,
            "_url": url, "_key": key, "_kind": kind,
            "_watched_at": datetime.now().isoformat(),
        })
        self.entries = self.entries[:self.MAX_ENTRIES]
        self._save()

    def remove(self, key: str, kind: str) -> None:
        self.entries = [e for e in self.entries
                        if not (e.get("_key") == key and e.get("_kind") == kind)]
        self._save()

    def clear(self) -> None:
        self.entries = []
        self._save()

    def items(self) -> list[dict]:
        return list(self.entries)


class ParentalControl:
    """A salted+hashed PIN gating locked categories and favorite groups."""

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings
        self.session_unlocked: bool = False

    def has_pin(self) -> bool:
        return bool(self.settings.value("parental_pin_hash", ""))

    @staticmethod
    def _hash(salt: str, pin: str) -> str:
        return hashlib.sha256((salt + pin).encode()).hexdigest()

    def set_pin(self, pin: str) -> None:
        salt = uuid.uuid4().hex
        self.settings.setValue("parental_salt", salt)
        self.settings.setValue("parental_pin_hash", self._hash(salt, pin))
        self.session_unlocked = False

    def clear_pin(self) -> None:
        self.settings.remove("parental_pin_hash")
        self.settings.remove("parental_salt")
        self.session_unlocked = False

    def verify(self, pin: str) -> bool:
        salt = self.settings.value("parental_salt", "")
        stored = self.settings.value("parental_pin_hash", "")
        return bool(stored) and self._hash(salt, pin) == stored

    def lock_session(self) -> None:
        self.session_unlocked = False


class CategoryOverrides:
    """Per-playlist category customizations (hide, rename, lock)."""

    def __init__(self, settings: QSettings,
                 key: str = "category_overrides") -> None:
        self.settings = settings
        self.key = key
        try:
            self.data: dict[str, dict] = json.loads(
                settings.value(key, "") or "{}")
        except Exception:
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}

    def _save(self) -> None:
        self.settings.setValue(self.key, json.dumps(self.data))

    def get(self, mode: str, cid: str | int) -> dict:
        return self.data.get(mode, {}).get(str(cid), {})

    def update(self, mode: str, cid: str | int, **fields: Any) -> None:
        entry = self.data.setdefault(mode, {}).setdefault(str(cid), {})
        entry.update(fields)
        if not any(entry.values()):
            del self.data[mode][str(cid)]
        self._save()

    def display_name(self, mode: str, cid: str | int,
                     default: str) -> str:
        return self.get(mode, cid).get("name") or default

    def is_hidden(self, mode: str, cid: str | int) -> bool:
        return bool(self.get(mode, cid).get("hidden"))

    def is_locked(self, mode: str, cid: str | int) -> bool:
        return bool(self.get(mode, cid).get("locked"))

    def excluded_ids(self, mode: str, include_locked: bool = True) -> set[str]:
        """Category ids whose contents should be excluded from 'All'."""
        out: set[str] = set()
        for cid, entry in self.data.get(mode, {}).items():
            if entry.get("hidden") or (include_locked and entry.get("locked")):
                out.add(str(cid))
        return out


class ChannelOverrides:
    """Per-playlist channel customizations (rename, hide)."""

    def __init__(self, settings: QSettings,
                 key: str = "channel_overrides") -> None:
        self.settings = settings
        self.key = key
        try:
            self.data: dict[str, dict] = json.loads(
                settings.value(key, "") or "{}")
        except Exception:
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}

    def _save(self) -> None:
        self.settings.setValue(self.key, json.dumps(self.data))

    def get(self, mode: str, key: str) -> dict:
        return self.data.get(mode, {}).get(str(key), {})

    def update(self, mode: str, key: str, **fields: Any) -> None:
        entry = self.data.setdefault(mode, {}).setdefault(str(key), {})
        entry.update(fields)
        if not any(entry.values()):
            del self.data[mode][str(key)]
        self._save()

    def display_name(self, mode: str, key: str, default: str) -> str:
        return self.get(mode, key).get("name") or default

    def is_hidden(self, mode: str, key: str) -> bool:
        return bool(self.get(mode, key).get("hidden"))

    def has_overrides(self, mode: str) -> bool:
        return bool(self.data.get(mode))

    def reset_mode(self, mode: str) -> None:
        self.data.pop(mode, None)
        self._save()


class PlaylistStore:
    """Multiple playlists/providers, persisted via QSettings.

    Migrates single-account legacy settings into a 'Default' playlist on
    first run.
    """

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings
        try:
            data = json.loads(settings.value("playlists", "") or "[]")
        except Exception:
            data = []
        self.items: list[dict] = data if isinstance(data, list) else []
        self.active_id: str = settings.value("active_playlist", "")
        if not self.items:
            server = settings.value("server", "")
            user = settings.value("username", "")
            pw = settings.value("password", "")
            if server and user and pw:
                self.items = [{"id": "default", "name": "Default",
                               "server": server, "username": user,
                               "password": pw, "epg_url": "",
                               "refresh": "never"}]
                self.active_id = "default"
                for legacy in ("favorites", "history"):
                    value = settings.value(legacy, "")
                    if value:
                        settings.setValue(f"{legacy}_default", value)
                self._save()

    def _save(self) -> None:
        self.settings.setValue("playlists", json.dumps(self.items))
        self.settings.setValue("active_playlist", self.active_id)

    def playlists(self) -> list[dict]:
        return list(self.items)

    def get(self, pid: str) -> dict | None:
        return next((p for p in self.items if p.get("id") == pid), None)

    def active(self) -> dict | None:
        return self.get(self.active_id) or (
            self.items[0] if self.items else None)

    def add(self, playlist: dict) -> dict:
        playlist.setdefault("id", uuid.uuid4().hex[:8])
        self.items.append(playlist)
        if not self.active_id:
            self.active_id = playlist["id"]
        self._save()
        return playlist

    def update(self, pid: str, **fields: Any) -> None:
        p = self.get(pid)
        if p:
            p.update(fields)
            self._save()

    def remove(self, pid: str) -> None:
        self.items = [p for p in self.items if p.get("id") != pid]
        if self.active_id == pid:
            self.active_id = self.items[0]["id"] if self.items else ""
        self._save()

    def set_active(self, pid: str) -> None:
        if self.get(pid):
            self.active_id = pid
            self._save()


class WatchedStore:
    """Watched-history cache with two overlaid layers: what Trakt says
    (synced from the account, wiped on each sync) and what the user has
    locally toggled 'seen' inside dopeIPTV (survives sync).

    The `is_*_watched` predicates return true if the tmdb id appears in
    either layer, so a Trakt-only user, a local-only user, and a hybrid
    user all get the same badge behaviour without having to think about
    which layer wrote it. Persisted as one JSON blob in QSettings so
    both layers survive shutdown and startup is a single read."""

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings
        self.trakt_movies: set[int] = set()
        self.local_movies: set[int] = set()
        # show_tmdb_id -> set of (season, episode) tuples
        self.trakt_episodes: dict[int, set[tuple[int, int]]] = {}
        self.local_episodes: dict[int, set[tuple[int, int]]] = {}
        # Local marks keyed on the provider's stream_id / series_id
        # instead of TMDB id, for when TMDB hasn't resolved (or has no
        # match at all - e.g. a sport rerun filed under Movies). Trakt
        # push is not available for these but the local badge is.
        self.local_movie_streams: set[int] = set()
        self.local_series_streams: set[int] = set()
        # Provider episode-id set for the same reason.
        self.local_episode_streams: set[int] = set()
        self.last_sync_at: int = 0
        self._load()

    # -- unions used by every predicate --------------------------------------

    @property
    def movies(self) -> set[int]:
        return self.trakt_movies | self.local_movies

    def episodes_for(self, show_tmdb_id: int) -> set[tuple[int, int]]:
        return ((self.trakt_episodes.get(show_tmdb_id) or set())
                | (self.local_episodes.get(show_tmdb_id) or set()))

    @property
    def episodes(self) -> dict[int, set[tuple[int, int]]]:
        keys = set(self.trakt_episodes) | set(self.local_episodes)
        return {k: self.episodes_for(k) for k in keys}

    # -- persistence ---------------------------------------------------------

    @staticmethod
    def _load_eps(raw) -> dict[int, set[tuple[int, int]]]:
        eps: dict[int, set[tuple[int, int]]] = {}
        for sid, pairs in (raw or {}).items():
            try:
                key = int(sid)
            except (ValueError, TypeError):
                continue
            eps[key] = {(int(s), int(e)) for s, e in pairs
                        if isinstance(s, int) and isinstance(e, int)}
        return eps

    def _load(self) -> None:
        raw = self.settings.value("trakt_watched_cache", "") or ""
        if not raw:
            return
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        # Back-compat: old versions stored a flat "movies"/"episodes"
        # (Trakt-only). Read those into the Trakt layer if the new
        # layered keys aren't present.
        self.trakt_movies = {
            int(x) for x in
            data.get("trakt_movies", data.get("movies", []))
            if isinstance(x, int)}
        self.local_movies = {int(x) for x in data.get("local_movies", [])
                             if isinstance(x, int)}
        self.trakt_episodes = self._load_eps(
            data.get("trakt_episodes") or data.get("episodes"))
        self.local_episodes = self._load_eps(data.get("local_episodes"))
        self.local_movie_streams = {
            int(x) for x in data.get("local_movie_streams", [])
            if isinstance(x, int)}
        self.local_series_streams = {
            int(x) for x in data.get("local_series_streams", [])
            if isinstance(x, int)}
        self.local_episode_streams = {
            int(x) for x in data.get("local_episode_streams", [])
            if isinstance(x, int)}
        self.last_sync_at = int(data.get("last_sync_at") or 0)

    def _save(self) -> None:
        def dump_eps(eps):
            return {str(k): sorted(list(v)) for k, v in eps.items()}
        payload = {
            "trakt_movies": sorted(self.trakt_movies),
            "local_movies": sorted(self.local_movies),
            "trakt_episodes": dump_eps(self.trakt_episodes),
            "local_episodes": dump_eps(self.local_episodes),
            "local_movie_streams": sorted(self.local_movie_streams),
            "local_series_streams": sorted(self.local_series_streams),
            "local_episode_streams": sorted(self.local_episode_streams),
            "last_sync_at": self.last_sync_at,
        }
        self.settings.setValue("trakt_watched_cache",
                               json.dumps(payload, separators=(",", ":")))

    # -- Trakt-sync layer ----------------------------------------------------

    def replace(self, movies: list[int],
                shows: dict[int, list[list[int]]]) -> None:
        """Rebuild the Trakt layer from a fresh sync payload. Leaves the
        local layer untouched."""
        self.trakt_movies = set(movies)
        self.trakt_episodes = {
            sid: {(s, e) for s, e in pairs}
            for sid, pairs in shows.items()
        }
        self.last_sync_at = int(datetime.now().timestamp())
        self._save()

    # -- local layer (right-click 'Mark as watched (local)') -----------------

    def mark_movie_local(self, tmdb_id: int) -> None:
        self.local_movies.add(int(tmdb_id))
        self._save()

    def unmark_movie(self, tmdb_id: int) -> None:
        """Remove from BOTH layers so the badge disappears immediately.
        The next Trakt sync will re-add it if it's still on Trakt - the
        caller decides whether to also POST /sync/history/remove."""
        tid = int(tmdb_id)
        self.trakt_movies.discard(tid)
        self.local_movies.discard(tid)
        self._save()

    def mark_episode_local(self, show_tmdb_id: int,
                           season: int, episode: int) -> None:
        eps = self.local_episodes.setdefault(int(show_tmdb_id), set())
        eps.add((int(season), int(episode)))
        self._save()

    def unmark_episode(self, show_tmdb_id: int,
                       season: int, episode: int) -> None:
        sid = int(show_tmdb_id)
        key = (int(season), int(episode))
        for layer in (self.trakt_episodes, self.local_episodes):
            eps = layer.get(sid)
            if eps and key in eps:
                eps.discard(key)
                if not eps:
                    del layer[sid]
        self._save()

    # -- predicates the delegate calls once per paint ------------------------

    def is_movie_watched(self, tmdb_id: int | None) -> bool:
        return isinstance(tmdb_id, int) and tmdb_id in self.movies

    def is_episode_watched(self, show_tmdb_id: int | None,
                           season: int | None,
                           episode: int | None) -> bool:
        if not isinstance(show_tmdb_id, int):
            return False
        if not isinstance(season, int) or not isinstance(episode, int):
            return False
        return (season, episode) in self.episodes_for(show_tmdb_id)

    def show_watched_count(self, show_tmdb_id: int | None) -> int:
        if not isinstance(show_tmdb_id, int):
            return 0
        return len(self.episodes_for(show_tmdb_id))

    # -- stream-id-based local marks (used when TMDB hasn't resolved) ---------

    def mark_movie_local_by_stream(self, stream_id: int) -> None:
        self.local_movie_streams.add(int(stream_id))
        self._save()

    def unmark_movie_by_stream(self, stream_id: int) -> None:
        self.local_movie_streams.discard(int(stream_id))
        self._save()

    def is_movie_watched_by_stream(self, stream_id: int | None) -> bool:
        return (isinstance(stream_id, int)
                and stream_id in self.local_movie_streams)

    def mark_series_local_by_stream(self, series_id: int) -> None:
        self.local_series_streams.add(int(series_id))
        self._save()

    def unmark_series_by_stream(self, series_id: int) -> None:
        self.local_series_streams.discard(int(series_id))
        self._save()

    def is_series_watched_by_stream(self, series_id: int | None) -> bool:
        return (isinstance(series_id, int)
                and series_id in self.local_series_streams)

    def mark_episode_local_by_stream(self, episode_id: int) -> None:
        self.local_episode_streams.add(int(episode_id))
        self._save()

    def unmark_episode_by_stream(self, episode_id: int) -> None:
        self.local_episode_streams.discard(int(episode_id))
        self._save()

    def is_episode_watched_by_stream(self, episode_id: int | None) -> bool:
        return (isinstance(episode_id, int)
                and episode_id in self.local_episode_streams)

    def clear(self) -> None:
        self.trakt_movies = set()
        self.local_movies = set()
        self.trakt_episodes = {}
        self.local_episodes = {}
        self.local_movie_streams = set()
        self.local_series_streams = set()
        self.local_episode_streams = set()
        self.last_sync_at = 0
        self.settings.remove("trakt_watched_cache")


class WatchlistStore:
    """'Watch later' list. Each entry is a full provider item dict so
    the sidebar's Watch Later category can render straight from the
    store without another vod_streams / series_list round-trip. Trakt
    ids come along for the ride so a Trakt-side add/remove has
    something to POST."""

    def __init__(self, settings: QSettings) -> None:
        self.settings = settings
        # kind ("movie" | "show") -> list of item dicts (newest first).
        self.movies: list[dict] = []
        self.shows: list[dict] = []
        # Sets of tmdb-ids known to Trakt (populated by replace()),
        # used to answer 'is on Trakt watchlist' for the badge in the
        # right-click menu.
        self.trakt_movies: set[int] = set()
        self.trakt_shows: set[int] = set()
        self.last_sync_at: int = 0
        self._load()

    def _load(self) -> None:
        raw = self.settings.value("trakt_watchlist_cache", "") or ""
        if not raw:
            return
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        self.movies = [x for x in (data.get("movies") or [])
                       if isinstance(x, dict)]
        self.shows = [x for x in (data.get("shows") or [])
                      if isinstance(x, dict)]
        self.trakt_movies = {int(x) for x in data.get("trakt_movies", [])
                             if isinstance(x, int)}
        self.trakt_shows = {int(x) for x in data.get("trakt_shows", [])
                            if isinstance(x, int)}
        self.last_sync_at = int(data.get("last_sync_at") or 0)

    def _save(self) -> None:
        payload = {
            "movies": self.movies,
            "shows": self.shows,
            "trakt_movies": sorted(self.trakt_movies),
            "trakt_shows": sorted(self.trakt_shows),
            "last_sync_at": self.last_sync_at,
        }
        self.settings.setValue("trakt_watchlist_cache",
                               json.dumps(payload, separators=(",", ":")))

    # -- Trakt-sync layer ---------------------------------------------------

    def replace(self, movies: list[int], shows: list[int]) -> None:
        """Rebuild the known-on-Trakt sets. Doesn't touch the item
        snapshots the user added locally - that's a separate concern."""
        self.trakt_movies = set(movies)
        self.trakt_shows = set(shows)
        self.last_sync_at = int(datetime.now().timestamp())
        self._save()

    # -- lookups ------------------------------------------------------------

    @staticmethod
    def _match(item: dict, tmdb_id: int | None,
               stream_id: int | None) -> bool:
        if isinstance(tmdb_id, int) and item.get("_tmdb_id") == tmdb_id:
            return True
        if isinstance(stream_id, int):
            for key in ("stream_id", "series_id"):
                if item.get(key) == stream_id:
                    return True
        return False

    def has_movie(self, tmdb_id: int | None,
                  stream_id: int | None = None) -> bool:
        if isinstance(tmdb_id, int) and tmdb_id in self.trakt_movies:
            return True
        return any(self._match(m, tmdb_id, stream_id) for m in self.movies)

    def has_show(self, tmdb_id: int | None,
                 stream_id: int | None = None) -> bool:
        if isinstance(tmdb_id, int) and tmdb_id in self.trakt_shows:
            return True
        return any(self._match(s, tmdb_id, stream_id) for s in self.shows)

    # -- add / remove ------------------------------------------------------

    @staticmethod
    def _make_entry(item: dict, tmdb_id: int | None) -> dict:
        """Store a shallow snapshot of just the fields the delegate
        and playback code touch, so a bulky provider payload doesn't
        blow up QSettings JSON."""
        snap = {
            "name": item.get("name") or item.get("title"),
            "title": item.get("title") or item.get("name"),
            "stream_id": item.get("stream_id"),
            "series_id": item.get("series_id"),
            "container_extension": item.get("container_extension"),
            "stream_icon": item.get("stream_icon"),
            "cover": item.get("cover"),
            "category_id": item.get("category_id"),
            "_tmdb_id": tmdb_id,
        }
        return {k: v for k, v in snap.items() if v is not None}

    def _dedup_key(self, item: dict, tmdb_id: int | None,
                   stream_id: int | None) -> None:
        pass

    def add_movie_local(self, item: dict, tmdb_id: int | None) -> None:
        if self.has_movie(tmdb_id, item.get("stream_id")):
            return
        self.movies.insert(0, self._make_entry(item, tmdb_id))
        self._save()

    def add_show_local(self, item: dict, tmdb_id: int | None) -> None:
        if self.has_show(tmdb_id, item.get("series_id")):
            return
        self.shows.insert(0, self._make_entry(item, tmdb_id))
        self._save()

    def remove_movie(self, tmdb_id: int | None,
                     stream_id: int | None = None) -> None:
        if isinstance(tmdb_id, int):
            self.trakt_movies.discard(tmdb_id)
        self.movies = [m for m in self.movies
                       if not self._match(m, tmdb_id, stream_id)]
        self._save()

    def remove_show(self, tmdb_id: int | None,
                    stream_id: int | None = None) -> None:
        if isinstance(tmdb_id, int):
            self.trakt_shows.discard(tmdb_id)
        self.shows = [s for s in self.shows
                      if not self._match(s, tmdb_id, stream_id)]
        self._save()

    def clear(self) -> None:
        self.movies = []
        self.shows = []
        self.trakt_movies = set()
        self.trakt_shows = set()
        self.last_sync_at = 0
        self.settings.remove("trakt_watchlist_cache")
