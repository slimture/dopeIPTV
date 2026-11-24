"""Start on the last channel: as dopeIPTV opens, the live channel the
profile in use watched last on the source in use plays again - so the app
starts as a TV does. Off by default, and each profile's own
(core/profiles.OWN_KEYS): on Settings > General, under Start.

This is what the window decides with, no Qt (ui/mw_start_channel.py does
the rest): whether it is on (``on``), which command the app was started
with goes instead (``skips``: any but TV mode alone), the channel History
names last (``last_live``: its newest live row - never an older one, and
none while History holds no channel; private viewing keeps nothing there)
and that channel among the source's channels as the lists show them
(``find``: by its stream id, nothing else). Nothing here plays, asks or
counts a connection: the window plays it through its one playback path,
as a click on the channel would.
"""
from __future__ import annotations

from typing import Any, Iterable

#: The profile's own switch ("true" / "false"; off when never set).
KEY = "start_last_channel"


def on(settings: Any) -> bool:
    """Whether the profile in use starts on its last channel."""
    try:
        return str(settings.value(KEY, "false") or "").strip().lower() == "true"
    except Exception:
        return False


def skips(cmd: Any) -> bool:
    """Whether the command the app was started with (core/commands.py)
    goes instead of the last channel: every one but ``--tv`` alone - a
    channel, an address or a link asked for wins, and a link that only
    opens the app is one asked for too."""
    return isinstance(cmd, dict) and bool(set(cmd) - {"tv"})


def stream_of(entry: Any) -> str:
    """The stream id a History row of a live channel names ("" for none)."""
    if not isinstance(entry, dict):
        return ""
    sid = entry.get("stream_id")
    if sid is None or sid == "":
        sid = entry.get("_key")
    if sid is None or isinstance(sid, (dict, list, tuple, set, bool)):
        return ""
    return str(sid).strip()[:200]


def last_live(entries: Iterable[Any] | None) -> dict | None:
    """History's newest live channel (its rows come newest first) - the
    newest only: when that one cannot play now, no older one plays
    instead."""
    for e in entries or ():
        if isinstance(e, dict) and e.get("_kind") == "live":
            return e if stream_of(e) else None
    return None


def find(rows: Iterable[Any] | None, entry: Any) -> dict | None:
    """The channel History's *entry* names among *rows* - the source's
    channels as its lists show them now - by its stream id; None when the
    lists do not hold it (hidden, of a hidden or locked category, gone)."""
    sid = stream_of(entry)
    if not sid:
        return None
    for r in rows or ():
        if (isinstance(r, dict) and not r.get("_header") and r.get("stream_id") is not None
                and str(r.get("stream_id")).strip() == sid):
            return r
    return None
