"""ResumeStore: remember how far into a title the user watched.

A small window-agnostic persistence service. It owns the resume-position
dict and its JSON storage in QSettings, plus the rule for when a position
is worth keeping. The window still reads the live position/duration from
the player and shows the "resume or restart?" dialog - this store only
decides what to persist and what offset a title should resume from.
"""

from __future__ import annotations

import json


class ResumeStore:
    # Playback kinds whose position is worth remembering, mapped to the
    # storage group prefix used in the persisted keys.
    _KIND_TO_GROUP = {"movie": "vod", "episode": "episode", "recording": "rec"}

    def __init__(self, settings, playlist_id: str | None) -> None:
        self._settings = settings
        self._key = (f"resume_positions_{playlist_id}" if playlist_id
                     else "resume_positions")
        try:
            data = json.loads(settings.value(self._key, "") or "{}")
        except (ValueError, TypeError):
            data = {}
        self._data: dict = data if isinstance(data, dict) else {}

    def record(self, group: str, key, pos: float, dur: float) -> None:
        """Store (or drop) a resume point for *group:key*. Positions in the
        first minute or past 95% of the runtime are dropped, not saved - the
        user is effectively at the start or the end."""
        rkey = f"{group}:{key}"
        if dur > 0 and 60 < pos < dur * 0.95:
            self._data[rkey] = {"pos": round(pos), "dur": round(dur)}
        else:
            self._data.pop(rkey, None)
        self._settings.setValue(self._key, json.dumps(self._data))

    def saved_position(self, key, kind: str) -> float:
        """The saved start offset in seconds for a title, or 0.0 when there
        is nothing worth resuming (unknown kind, no saved point, or a point
        too near the start)."""
        group = self._KIND_TO_GROUP.get(kind)
        saved = self._data.get(f"{group}:{key}") if group else None
        if not saved:
            return 0.0
        pos = float(saved.get("pos") or 0)
        return pos if pos > 60 else 0.0
