"""ReminderStore: 'remind me when this programme starts'.

A tiny persistence service, playlist-scoped like ResumeStore. It keeps a list
of EPG reminders (a minimal channel snapshot + programme title + start time)
in QSettings, and hands back the ones whose start has just arrived so the
window can notify the user and offer to tune in.

A reminder a series rule set (core/series_rules.py, "Remind me") also keeps
the rule's id and its programme's end: the rule's reminders go with it, and
one follows its programme when the guide moves it.
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

    def add(self, ch: dict, title: str, start, rule: str | None = None,
            stop=None) -> None:
        """A reminder for *title* on *ch* at *start*; one already there for
        that channel and start is replaced. *rule*: the series rule that set
        it, *stop* its programme's end."""
        sid = ch.get("stream_id")
        start = int(start)
        self._items = [r for r in self._items
                       if not (r.get("stream_id") == sid
                               and r.get("start") == start)]
        item = {
            "stream_id": sid,
            "ch": {k: ch[k] for k in _CH_FIELDS if ch.get(k) is not None},
            "title": title or "",
            "start": start,
        }
        if rule:
            item["rule"] = str(rule)
            if stop is not None:
                item["stop"] = int(stop)
        self._items.append(item)
        self._save()

    def remove_rule(self, rule_id: str, now: float) -> int:
        """The upcoming reminders the series rule *rule_id* set, removed
        with it. How many went."""
        keep = [r for r in self._items
                if not (r.get("rule") == rule_id
                        and int(r.get("start") or 0) > now)]
        gone = len(self._items) - len(keep)
        if gone:
            self._items = keep
            self._save()
        return gone

    def move(self, sid, start, new_start, new_stop=None) -> bool:
        """The reminder for *sid* at *start* moved to *new_start* (its
        programme moved in the guide); False when there is none."""
        start, new_start = int(start), int(new_start)
        for r in self._items:
            if r.get("stream_id") == sid and r.get("start") == start:
                self._items = [o for o in self._items
                               if o is r or not (o.get("stream_id") == sid
                                                 and o.get("start") == new_start)]
                r["start"] = new_start
                if new_stop is not None:
                    r["stop"] = int(new_stop)
                self._save()
                return True
        return False

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
        clean = []
        for r in self._items:
            row = {"stream_id": r.get("stream_id"), "ch": r.get("ch"),
                   "title": r.get("title"), "start": r.get("start")}
            for k in ("rule", "stop"):
                if r.get(k) is not None:
                    row[k] = r[k]
            clean.append(row)
        self._settings.setValue(self._key, json.dumps(clean))
