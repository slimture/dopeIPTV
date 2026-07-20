"""ReminderStore: 'remind me when this programme starts'.

A tiny persistence service, playlist-scoped like ResumeStore. It keeps a list
of EPG reminders (a minimal channel snapshot + programme title + start time)
in QSettings, and hands back the ones whose start has just arrived so the
window can notify the user and offer to tune in.
"""

from __future__ import annotations

import json

# Channel fields kept so a reminder can tune the channel when it fires.
_CH_FIELDS = ("name", "num", "stream_id", "stream_icon",
              "epg_channel_id", "category_id")
# How late a reminder may fire before it counts as missed and is dropped
# silently (e.g. the app was closed when the programme started).
_GRACE_SECS = 300


class ReminderStore:
    """Persisted set of programme reminders (JSON-backed on disk)."""
    def __init__(self, settings, playlist_id: str | None = None) -> None:
        self._settings = settings
        self._key = (f"epg_reminders_{playlist_id}" if playlist_id
                     else "epg_reminders")
        try:
            data = json.loads(settings.value(self._key, "") or "[]")
        except (ValueError, TypeError):
            data = []
        self._items: list[dict] = data if isinstance(data, list) else []

    def add(self, ch: dict, title: str, start) -> None:
        sid = ch.get("stream_id")
        start = int(start)
        self._items = [r for r in self._items
                       if not (r.get("stream_id") == sid
                               and r.get("start") == start)]
        self._items.append({
            "stream_id": sid,
            "ch": {k: ch[k] for k in _CH_FIELDS if ch.get(k) is not None},
            "title": title or "",
            "start": start,
        })
        self._save()

    def remove(self, sid, start) -> None:
        start = int(start)
        before = len(self._items)
        self._items = [r for r in self._items
                       if not (r.get("stream_id") == sid
                               and r.get("start") == start)]
        if len(self._items) != before:
            self._save()

    def has(self, sid, start) -> bool:
        start = int(start)
        return any(r.get("stream_id") == sid and r.get("start") == start
                   for r in self._items)

    def all(self) -> list[dict]:
        # Copies, so a caller decorating a row (e.g. a UI countdown label)
        # can't mutate - or make unserialisable - the stored reminders.
        return [dict(r) for r in self._items]

    def due(self, now: int) -> list[dict]:
        """Reminders whose start has arrived (within the grace window). Fired
        and missed reminders are both removed; upcoming ones are kept."""
        due, keep = [], []
        for r in self._items:
            st = int(r.get("start") or 0)
            if st > now:
                keep.append(r)
            elif now - st <= _GRACE_SECS:
                due.append(r)
            # else: missed -> drop silently
        if len(keep) != len(self._items):
            self._items = keep
            self._save()
        return due

    def _save(self) -> None:
        # Persist only the known fields, so a stray non-serialisable key that
        # some caller may have dropped onto an item (e.g. a Qt widget) can never
        # break saving.
        clean = [{"stream_id": r.get("stream_id"), "ch": r.get("ch"),
                  "title": r.get("title"), "start": r.get("start")}
                 for r in self._items]
        self._settings.setValue(self._key, json.dumps(clean))
