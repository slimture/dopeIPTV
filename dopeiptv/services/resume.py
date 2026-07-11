"""ResumeStore: remember how far into a title the user watched.

A small window-agnostic persistence service. It owns the resume-position
dict and its JSON storage in QSettings, plus the rule for when a position
is worth keeping. The window still reads the live position/duration from
the player and shows the "resume or restart?" dialog - this store only
decides what to persist and what offset a title should resume from.
"""

from __future__ import annotations

import json
import time

# Fields worth keeping in a resume snapshot so the "Continue watching" list
# can render a row (and replay it) without re-fetching the provider.
_SNAP_FIELDS = ("name", "title", "stream_id", "container_extension",
                "stream_icon", "cover", "category_id", "_tmdb_id")


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

    def record(self, group: str, key, pos: float, dur: float,
               item: dict | None = None) -> None:
        """Store (or drop) a resume point for *group:key*. Positions in the
        first minute or past 95% of the runtime are dropped, not saved - the
        user is effectively at the start or the end. For movies an *item*
        snapshot is kept so the 'Continue watching' list can render/replay it."""
        rkey = f"{group}:{key}"
        if dur > 0 and 60 < pos < dur * 0.95:
            entry = {"pos": round(pos), "dur": round(dur),
                     "ts": int(time.time())}
            if group == "vod" and item:
                snap = {k: item[k] for k in _SNAP_FIELDS
                        if item.get(k) is not None}
                if snap:
                    entry["item"] = snap
            else:
                # Preserve any existing snapshot when we only have position.
                old = self._data.get(rkey) or {}
                if old.get("item"):
                    entry["item"] = old["item"]
            self._data[rkey] = entry
        else:
            self._data.pop(rkey, None)
        self._settings.setValue(self._key, json.dumps(self._data))

    def continue_watching(self) -> list[dict]:
        """Partly-watched movies as list rows, newest first. Each row carries
        the saved snapshot plus _resume_pos / _resume_dur / _progress_pct so
        the delegate can draw a progress bar and playback can resume."""
        out: list[dict] = []
        for rkey, v in self._data.items():
            if not rkey.startswith("vod:") or not isinstance(v, dict):
                continue
            snap = v.get("item")
            if not snap:
                continue
            dur = float(v.get("dur") or 0)
            pos = float(v.get("pos") or 0)
            row = dict(snap)
            row["_kind"] = "vod"
            row["_resume_pos"] = pos
            row["_resume_dur"] = dur
            row["_progress_pct"] = round(100 * pos / dur) if dur else 0
            row["_ts"] = int(v.get("ts") or 0)
            out.append(row)
        out.sort(key=lambda r: r.get("_ts", 0), reverse=True)
        return out

    def clear(self, group: str, key) -> None:
        """Drop a single resume point (e.g. 'Remove from Continue watching')."""
        if self._data.pop(f"{group}:{key}", None) is not None:
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
