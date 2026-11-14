"""Private viewing and clearing what was watched, as a browser does.

Private viewing lasts until the app closes (as a private browser window
does): while it is on, nothing of what is played is kept - no Recently
viewed, no Continue-watching position, no watched mark, no Trakt scrobble,
no rewind buffer kept past the session. The stores check it themselves
(HistoryStore.add, ResumeStore.record), so no place that plays something
can forget to.

Clearing takes a time range - the last hour, 3 hours, 24 hours, 5 days or
all of it - and what to clear: channels, movies and episodes, and where
playback stopped.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from .log import log

# (id, seconds back; 0 = everything), in the order a menu shows them.
RANGES: tuple[tuple[str, int], ...] = (
    ("1h", 3600), ("3h", 3 * 3600), ("24h", 24 * 3600), ("5d", 5 * 24 * 3600), ("all", 0))
CHANNEL_KINDS = frozenset({"live", "catchup"})
TITLE_KINDS = frozenset({"movie", "episode", "series", "vod", "recording", "local"})

_private = False


def set_private(on: bool) -> None:
    global _private
    if on != _private:
        log.info("private viewing %s", "on" if on else "off")
    _private = bool(on)


def private() -> bool:
    return _private


def for_log(text: Any) -> Any:
    """What a log line may say of what is played: its title or name, or
    "<private>" while private viewing is on - the log is kept on disk and
    goes with a report."""
    return "<private>" if _private else text


def cutoff(seconds: int, now: float | None = None) -> float | None:
    """The moment from which to clear (a Unix time), or None for all."""
    if seconds <= 0:
        return None
    return (time.time() if now is None else now) - seconds


def _when(entry: dict) -> float | None:
    stamp = entry.get("_watched_at")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def clear_history(history: Any, since: float | None,
                  kinds: frozenset[str] | set[str] | None = None) -> int:
    """Take out of Recently viewed what was played since *since* (all of it
    for None), of *kinds* only when given. Returns how many went. An entry
    with no time it was played goes only when everything is cleared."""
    keep, gone = [], 0
    for e in history.items():
        kind_hit = kinds is None or e.get("_kind") in kinds
        at = _when(e)
        time_hit = since is None or (at is not None and at >= since)
        if kind_hit and time_hit:
            gone += 1
        else:
            keep.append(e)
    if gone:
        history.entries = keep
        history._save()
    return gone


def resume_keys(settings: Any) -> list[str]:
    """Every source's stored positions (one key per source)."""
    try:
        keys = settings.allKeys()
    except Exception:
        return []
    return [k for k in keys if str(k).startswith("resume_positions")]


def clear_resume(settings: Any, since: float | None) -> int:
    """Forget where playback stopped, for every source, since *since* (all
    for None). Returns how many positions went."""
    gone = 0
    for key in resume_keys(settings):
        try:
            data = json.loads(settings.value(key, "") or "{}")
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            continue
        keep = {k: v for k, v in data.items()
                if isinstance(v, dict) and since is not None and float(v.get("ts") or 0) < since}
        gone += len(data) - len(keep)
        if len(keep) != len(data):
            settings.setValue(key, json.dumps(keep))
    return gone


def history_keys(settings: Any) -> list[str]:
    """Every source's Recently viewed (one key per source, "history" for
    the first ever)."""
    try:
        keys = settings.allKeys()
    except Exception:
        return []
    return [k for k in keys if k == "history" or str(k).startswith("history_")]


def clear_recent(settings: Any, resume_settings: Any, history: Any, seconds: int,
                 channels: bool = True, titles: bool = True,
                 positions: bool = True) -> dict[str, int]:
    """Clear what was watched in the last *seconds* (0: all), for every
    source - *history* is the active one's store, which the window shows.
    Returns the counts per part, for the note that says what went."""
    from .stores import HistoryStore
    since = cutoff(seconds)
    out = {"channels": 0, "titles": 0, "positions": 0}
    stores = [history]
    for key in history_keys(settings):
        if key != getattr(history, "key", None):
            stores.append(HistoryStore(settings, key))
    for store in stores:
        if channels:
            out["channels"] += clear_history(store, since, CHANNEL_KINDS)
        if titles:
            out["titles"] += clear_history(store, since, TITLE_KINDS)
        if channels and titles:
            # Whatever kind is left over (none known today) goes with both.
            out["titles"] += clear_history(store, since, None)
    if positions:
        out["positions"] = clear_resume(resume_settings, since)
    log.info("cleared recent history (%s): %s", seconds or "all", out)
    return out
