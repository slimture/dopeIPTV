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
