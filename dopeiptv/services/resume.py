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
from typing import Any

# Fields worth keeping in a resume snapshot so the "Continue watching" list
# can render a row (and replay it) without re-fetching the provider.
_SNAP_FIELDS = ("name", "title", "stream_id", "series_id", "id",
                "container_extension", "stream_icon", "cover", "category_id",
                "season", "episode_num", "_tmdb_id")
# Series-context fields kept for an episode so it can be replayed from the
# flat Continue-watching list (episode_url needs the series id) and so the row
# can show the series' name + artwork instead of a bare episode title.
# category_id lets backing out of the episode list land in the series' own
# category after a continue-watching drill.
_CTX_FIELDS = ("series_id", "name", "title", "cover", "stream_icon",
               "category_id", "_tmdb_id")


class ResumeStore:
    """Persisted resume points backing 'Continue watching' (JSON on disk)."""
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
               item: dict | None = None,
               series_ctx: dict | None = None) -> None:
        """Store (or drop) a resume point for *group:key*. Positions in the
        first minute or past 95% of the runtime are dropped, not saved - the
        user is effectively at the start or the end. For movies and episodes an
        *item* snapshot is kept (plus the episode's *series_ctx*) so the
        'Continue watching' list can render and replay it."""
        rkey = f"{group}:{key}"
        if dur > 0 and 60 < pos < dur * 0.95:
            entry: dict[str, Any] = {"pos": round(pos), "dur": round(dur),
                                     "ts": int(time.time())}
            if group in ("vod", "episode") and item:
                snap = {k: item[k] for k in _SNAP_FIELDS
                        if item.get(k) is not None}
                if snap:
                    entry["item"] = snap
                if group == "episode" and series_ctx:
                    ctx = {k: series_ctx[k] for k in _CTX_FIELDS
                           if series_ctx.get(k) is not None}
                    if ctx:
                        entry["ctx"] = ctx
            else:
                # Preserve any existing snapshot when we only have position.
                old = self._data.get(rkey) or {}
                if old.get("item"):
                    entry["item"] = old["item"]
                if old.get("ctx"):
                    entry["ctx"] = old["ctx"]
            self._data[rkey] = entry
        else:
            self._data.pop(rkey, None)
        self._settings.setValue(self._key, json.dumps(self._data))

    def continue_watching(self) -> list[dict]:
        """Partly-watched movies and episodes as list rows, newest first. Each
        row carries the saved snapshot plus _resume_pos / _resume_dur /
        _progress_pct (for the delegate's progress bar); episode rows also carry
        _series_ctx so they can be replayed from this flat list."""
        out: list[dict] = []
        for rkey, v in self._data.items():
            if not isinstance(v, dict):
                continue
            if rkey.startswith("vod:"):
                kind = "vod"
            elif rkey.startswith("episode:"):
                kind = "episode"
            else:
                continue
            snap = v.get("item")
            if not snap:
                continue
            dur = float(v.get("dur") or 0)
            pos = float(v.get("pos") or 0)
            row = dict(snap)
            row["_kind"] = kind
            row["_resume_pos"] = pos
            row["_resume_dur"] = dur
            row["_progress_pct"] = round(100 * pos / dur) if dur else 0
            row["_ts"] = int(v.get("ts") or 0)
            if kind == "episode":
                ctx = v.get("ctx") or {}
                row["_series_ctx"] = ctx
                # Episodes rarely carry their own artwork/name, so borrow the
                # series' so the row shows which show it is (poster + name).
                art = ctx.get("cover") or ctx.get("stream_icon")
                if art:
                    row.setdefault("stream_icon", art)
                    row.setdefault("cover", art)
                if ctx.get("_tmdb_id") is not None:
                    row.setdefault("_tmdb_id", ctx["_tmdb_id"])
                sname = ctx.get("name") or ctx.get("title")
                ep = row.get("name") or ""
                if sname:
                    row["name"] = f"{sname} · {ep}" if ep else sname
                    # The cover pipeline resolves an episode's poster from the
                    # SERIES title (searching TMDB for "Show · S1 * E2 - ..."
                    # never matches). Drilled episode lists stamp this in
                    # _enter_series; stamp it here too so a Continue-watching
                    # row resolves the same stable series poster.
                    row.setdefault("_series_title", sname)
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
