"""Private viewing and clearing what was watched, as a browser does.

Private viewing lasts until the app closes (as a private browser window
does): while it is on, nothing of what is played is kept - no Recently
viewed, no Continue-watching position, no watched mark, no Trakt scrobble,
no rewind buffer kept past the session. The stores check it themselves
(HistoryStore.add, ResumeStore.record), so no place that plays something
can forget to.

Clearing takes a time range - the last hour, 3 hours, 24 hours, 5 days or
all of it - and what to clear: channels, movies and episodes, and where
playback stopped. The time counted for the viewing statistics
(core/viewing_stats.py) goes with the channels and the titles cleared.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Any

from .log import log, redact_url

# (id, seconds back; 0 = everything), in the order a menu shows them.
RANGES: tuple[tuple[str, int], ...] = (
    ("1h", 3600), ("3h", 3 * 3600), ("24h", 24 * 3600), ("5d", 5 * 24 * 3600), ("all", 0))
CHANNEL_KINDS = frozenset({"live", "catchup"})
TITLE_KINDS = frozenset({"movie", "episode", "series", "vod", "recording", "local"})

_private = False
# Woken as private viewing is turned on or off: the cast and AirPlay
# bridges' watchers (wait_change) stop a converter writing to the disk the
# moment it begins, not at their next look a second later.
_changed = threading.Condition()


def set_private(on: bool) -> None:
    global _private
    if on != _private:
        log.info("private viewing %s", "on" if on else "off")
    _private = bool(on)
    with _changed:
        _changed.notify_all()


def wait_change(timeout: float) -> None:
    """Wait *timeout* seconds, or less: until private viewing is turned on
    or off."""
    with _changed:
        _changed.wait(timeout)


def private() -> bool:
    return _private


# The playback (kind, key) that was private at some point, and the names it
# goes by (folded) - what nothing is asked about elsewhere while it lasts. The same playback
# going on after private viewing ends - a reconnect, a backup address, a seek
# in the archive - stays private to the end of its run, for every reporter
# (Discord, the media servers, Watch together, the statistics, the events).
_private_playback: tuple[str, str] | None = None
_private_titles: frozenset[str] = frozenset()
# A playback kept nowhere though private viewing is off: the demo's
# (providers/demo.py, its client's ``keeps_nothing``). Every reporter that
# asks playback_private or private_key keeps and sends nothing of it, as of
# a private one; nothing else changes for it - playing_private() does not
# count it, so no buffer is held in memory and no cast is refused for it.
_passing_playback: tuple[str, str] | None = None


def _key(key: Any) -> str:
    """A title's key as one kind of value: a provider gives the same id as a
    number in one list and as text in another."""
    return "" if key is None else str(key)


def playback_started(kind: str, key: Any, keep: bool = True) -> None:
    """The window's player took a stream (or private viewing was turned on
    while one plays): marked private while private viewing is on; anything
    else chosen after it ended is not. *keep* False: a playback kept
    nowhere whatever private viewing says (the demo's)."""
    global _private_playback, _private_titles, _passing_playback
    ident = (str(kind or ""), _key(key))
    if _private_playback != ident:
        _private_titles = frozenset()
    if private():
        _private_playback = ident
    elif _private_playback != ident:
        _private_playback = None
    _passing_playback = None if keep else ident


def playback_stopped() -> None:
    """Stop: what is played next is a new playback."""
    global _private_playback, _private_titles, _passing_playback
    _passing_playback = None
    if not private():
        _private_playback = None
        _private_titles = frozenset()


def playback_private(kind: str, key: Any) -> bool:
    """Whether the playback (*kind*, *key*) is to be kept to this computer -
    or, the demo's, kept nowhere."""
    ident = (str(kind or ""), _key(key))
    return private() or _private_playback == ident or _passing_playback == ident


def private_key(key: Any) -> bool:
    """Whether a store keeping something of the title *key* - a resume
    point - is to keep nothing: private viewing on, or that title the
    private playback still going on (whatever kind the store calls it)."""
    return private() or any(p is not None and p[1] == _key(key)
                            for p in (_private_playback, _passing_playback))


def playing_private() -> bool:
    """Whether what the window's player (or a cast) plays is private: private
    viewing on, or a playback that began in it still going on. For those
    that know no kind and key - the player itself, the log."""
    return _private or _private_playback is not None


def _fold(title: Any) -> str:
    return str(title or "").strip().lower()


def playback_titles(*names: Any) -> None:
    """The names the playback just started goes by - its title, its series,
    a file's cleaned name. Kept only for a private one, and only to answer
    title_private()."""
    global _private_titles
    _private_titles = (frozenset(f for f in map(_fold, names) if f)
                       if _private_playback is not None or _passing_playback is not None
                       else frozenset())


def title_private(title: Any) -> bool:
    """Whether *title* is what plays privately now: nothing is to be asked
    about it elsewhere (TMDB), though the lists around it still fetch
    their posters."""
    return bool(_private_titles) and _fold(title) in _private_titles


def for_log(text: Any) -> Any:
    """What a log line may say of what is played: its title or name, or
    "<private>" while private viewing is on or a playback that began
    private still goes on after it - the log is kept on disk and goes with
    a report."""
    return "<private>" if playing_private() else text


def url_for_log(url: Any) -> str:
    """A stream's address as a log line may carry it: its account taken out
    (core/log.redact_url) - and, for what plays privately, nothing at all:
    not the server's name either, which says whose service it is."""
    if not playing_private():
        return redact_url(url)
    return "<private>"


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
                 positions: bool = True, viewing: Any = None) -> dict[str, int]:
    """Clear what was watched in the last *seconds* (0: all), for every
    source - *history* is the active one's store, which the window shows.
    The viewing statistics' time goes with them: *viewing* is the log the
    window counts into (one is read from *settings* without it). Returns
    the counts per part, for the note that says what went."""
    from .stores import HistoryStore
    from . import viewing_stats
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
    # The first profile's history as the first start with profiles left it
    # under the old names (core/profiles.left_behind): cleared the same, not
    # counted - it is not what the window shows.
    from . import profiles
    old = profiles.left_behind(settings)
    if old is not None:
        for key in history_keys(old):
            store = HistoryStore(old, key)
            if channels:
                clear_history(store, since, CHANNEL_KINDS)
            if titles:
                clear_history(store, since, TITLE_KINDS)
            if channels and titles:
                clear_history(store, since, None)
    old = profiles.left_behind(resume_settings)
    if positions and old is not None:
        clear_resume(old, since)
    kinds: set[str] = set()
    if channels:
        kinds.add(viewing_stats.CHANNEL)
    if titles:
        kinds.update((viewing_stats.FILM, viewing_stats.SERIES, viewing_stats.OTHER))
    if kinds:
        vlog = viewing if viewing is not None else viewing_stats.ViewingLog(settings)
        if vlog.clear(since, kinds):
            vlog.save()
    if channels:
        # The channels' last observed stream details say which were watched.
        # They carry no time, so any clear of channels takes all of them: a
        # cache that fills again as channels play.
        from . import stream_info
        if settings.value(stream_info.KEY) is not None:
            settings.setValue(stream_info.KEY, "{}")
    log.info("cleared recent history (%s): %s", seconds or "all", out)
    return out
