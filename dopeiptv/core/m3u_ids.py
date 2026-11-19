"""Stable channel ids for M3U sources.

An M3U list's channels used to be numbered by their place in it: one
channel added at the top and every favourite, hidden or renamed channel,
own number and reminder pointed at the channel next to it. A channel's id is
now made from the channel itself - its guide id (``tvg-id``) and name, and
which of the channels with both alike it is - so it is the same after the
provider adds, removes or reorders channels, and the same on every computer
(favourites and history sync by it).

The move happens in two starts, so nothing is ever shown half moved:

1. A source not moved yet is read as before (ids by place). After each
   read its client writes a map of each place to its new id, with the
   channel's name and address (``save_map``).
2. At the next start, before any store is read (``start``), every value
   that names one of the source's channels by place - in every profile - is
   given the new id (``migrate``): by the address kept with it where that
   names one channel, else by the name kept with it, else by its place (what
   it showed until now). The source is then marked moved
   (``m3u_stable_<source id>``, this computer's) and read with the new ids.

New ids are never below ``LINE_MAX``, ids by place always are: a value
already moved is never moved again, and an old id that still turns up (from
an older version on another computer) plays the channel at that place
(``M3UClient``'s alias).
"""

from __future__ import annotations

import json
import os
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import profiles
from .log import log
from .source_files import file_part

#: Ids by place are below it, stable ids from it up (and below 2**31).
LINE_MAX = 1 << 30
#: This computer's mark that a source is read with stable ids.
FLAG_PREFIX = "m3u_stable_"

#: The sources read with stable ids this run (``start``).
_STABLE: set[str] = set()


class Settings(Protocol):
    def value(self, key: str, *args: Any, **kwargs: Any) -> Any: ...
    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name
    def allKeys(self) -> list[str]: ...  # noqa: N802 - Qt's name


def channel_key(tvg_id: str, name: str) -> str:
    """What names a channel in an M3U list, whatever its place."""
    return f"{(tvg_id or '').strip()}\x1f{' '.join((name or '').split())}"


def stable_ids(keys: Iterable[str]) -> list[int]:
    """Each channel's id from its key (channel_key) and which of the
    channels with that key it is, in list order. Two keys that hash alike
    take the next free id in list order.

    The next free id is found through *after* - a taken id to the one to
    look at next, every id between them taken, shortened as it is walked -
    not by trying each id in turn: a list made to hash alike (CRC-32 is
    easily steered) would take a step for every channel before it, each
    time. The ids are the same either way."""
    after: dict[int, int] = {}
    seen: dict[str, int] = {}
    out: list[int] = []
    for key in keys:
        n = seen.get(key, 0)
        seen[key] = n + 1
        sid = LINE_MAX | (zlib.crc32(f"{key}\x1f{n}".encode()) & (LINE_MAX - 1))
        walked = []
        while sid in after:
            walked.append(sid)
            sid = after[sid]
        for w in walked:
            after[w] = sid
        after[sid] = LINE_MAX | ((sid + 1) & (LINE_MAX - 1))
        out.append(sid)
    return out


def line_id(value: Any) -> int | None:
    """*value* as an id by place, else None (a stable id, or not an id)."""
    if isinstance(value, bool):
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 < n < LINE_MAX else None


def _fold(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


@dataclass
class Remap:
    """One source's places and their new ids, from the list as last read."""

    by_line: dict[int, int] = field(default_factory=dict)
    name_of: dict[int, str] = field(default_factory=dict)
    by_name: dict[str, list[int]] = field(default_factory=dict)
    by_url: dict[str, list[int]] = field(default_factory=dict)

    @classmethod
    def of(cls, rows: Iterable[tuple[int, int, str, str]]) -> Remap:
        """From (place, new id, name, address) rows."""
        m = cls()
        for line, sid, name, url in rows:
            m.by_line[int(line)] = int(sid)
            m.name_of[int(sid)] = _fold(name)
            m.by_name.setdefault(_fold(name), []).append(int(sid))
            if url:
                m.by_url.setdefault(str(url), []).append(int(sid))
        return m

    def new_id(self, old: Any, name: Any = "", url: Any = "") -> int | None:
        """The new id for *old*, an id by place (None for anything else, or a
        place past the list): the channel at the address kept with it, else
        the one of the name kept with it, else the one at that place."""
        line = line_id(old)
        if line is None:
            return None
        at = self.by_line.get(line)
        hits = self.by_url.get(str(url)) if url else None
        if hits and len(hits) == 1:
            return hits[0]
        folded = _fold(name)
        if folded:
            if at is not None and self.name_of.get(at) == folded:
                return at
            named = self.by_name.get(folded)
            if named:
                return named[0]
        return at


def map_path(cache_dir: str, pid: str) -> str:
    return os.path.join(cache_dir, f"m3u_ids_{file_part(pid)}.json")


def save_map(path: str, rows: list[tuple[int, int, str, str]]) -> None:
    """The map of a source read by place, for the next start."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"v": 1, "rows": rows}, fh, ensure_ascii=False,
                      separators=(",", ":"))
        os.replace(tmp, path)
    except OSError as e:
        log.debug("m3u ids: map not written: %s", e)


def load_map(path: str) -> Remap | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    good = [r for r in rows if isinstance(r, list) and len(r) == 4]
    try:
        return Remap.of((int(a), int(b), str(c), str(d)) for a, b, c, d in good)
    except (TypeError, ValueError):
        return None


# -- the move -----------------------------------------------------------------------------

def _load(raw: Any, default: Any) -> Any:
    if not isinstance(raw, (str, bytes)) or not raw:
        return default
    try:
        got = json.loads(raw)
    except ValueError:
        return default
    return got if isinstance(got, type(default)) else default


class _Pass:
    """One source's values moved, or (without a remap) only counted."""

    def __init__(self, remap: Remap | None) -> None:
        self.remap = remap
        self.found = 0
        self.moved = 0

    def new(self, old: Any, name: Any = "", url: Any = "") -> Any:
        """*old* as it is to be kept: its new id in *old*'s own type, or
        *old* itself."""
        if line_id(old) is None:
            return old
        self.found += 1
        sid = self.remap.new_id(old, name, url) if self.remap else None
        if sid is None:
            return old
        self.moved += 1
        return str(sid) if isinstance(old, str) else sid

    def keys(self, d: dict, prefix: str = "", named: str = "") -> dict:
        """*d* with each key that names a channel by place given its new id
        (*prefix*: the part before the id, "<source>:"; *named*: the field
        of a value that holds the channel's name). A key the new one is
        already taken by keeps its own value; the order stays."""
        out: dict = {}
        for k, v in d.items():
            ks = str(k)
            if prefix and not ks.startswith(prefix):
                out[k] = v
                continue
            name = v.get(named) if named and isinstance(v, dict) else ""
            nk = self.new(ks[len(prefix):], name)
            if nk == ks[len(prefix):]:
                out[k] = v              # a key moved before it gives way
            elif prefix + str(nk) not in out:
                out[prefix + str(nk)] = v
        return out

    def rows(self, rows: list, id_key: str = "stream_id") -> None:
        for r in rows:
            if isinstance(r, dict) and id_key in r:
                r[id_key] = self.new(r[id_key], r.get("name"),
                                     r.get("_url") or r.get("url"))


def _move_value(base: str, data: Any, pid: str, p: _Pass) -> Any:
    """*data* (a stored value of key *base*) with the source's channels
    moved; None when the key holds none of them."""
    if base == f"favorites_{pid}":
        groups = _load(data, {})
        for rows in groups.values():
            if isinstance(rows, list):
                p.rows(rows)
        return groups
    if base == f"history_{pid}":
        entries = _load(data, [])
        for e in entries:
            if not isinstance(e, dict) or e.get("_kind") != "live":
                continue
            url, name = e.get("_url"), e.get("name")
            if "stream_id" in e:
                e["stream_id"] = p.new(e["stream_id"], name, url)
            if "_key" in e:
                e["_key"] = p.new(e["_key"], name, url)
        return entries
    if base == f"channel_overrides_{pid}":
        ov = _load(data, {})
        if isinstance(ov.get("live"), dict):
            ov["live"] = p.keys(ov["live"])
        return ov
    if base in (f"nerd_chno_{pid}", f"nerd_epg_map_{pid}"):
        return p.keys(_load(data, {}))
    if base == f"epg_reminders_{pid}":
        rows = _load(data, [])
        p.rows(rows)
        return rows
    if base == f"series_rules_{pid}":
        store = _load(data, {})
        for rule in store.get("rules") or []:
            ch = rule.get("channel") if isinstance(rule, dict) else None
            if isinstance(ch, dict) and "stream_id" in ch:
                ch["stream_id"] = p.new(ch["stream_id"], ch.get("name"))
        return store
    if base in ("nerd_channel_prefs", "nerd_health_data"):
        return p.keys(_load(data, {}), f"{pid}:")
    if base == "viewing_stats":
        log_ = _load(data, {})
        if isinstance(log_.get("t"), dict):
            log_["t"] = p.keys(log_["t"], f"live:{pid}:", named="n")
        return log_
    if base == "recording_jobs":
        jobs = _load(data, [])
        for j in jobs:
            if isinstance(j, dict) and str(j.get("source") or "") == pid \
                    and "stream_id" in j:
                j["stream_id"] = p.new(j["stream_id"], j.get("channel"), j.get("url"))
        return jobs
    return None


def migrate(settings: Settings, pid: str, remap: Remap | None) -> tuple[int, int]:
    """Every value of *settings* (each profile's too) that names a channel
    of source *pid* by place given its new id; without *remap* only counted.
    (found, moved): how many ids by place there were, how many moved."""
    p = _Pass(remap)
    for key in list(settings.allKeys()):
        got = profiles.split_key(key)
        base = got[1] if got is not None else key
        raw = settings.value(key, "")
        before = (p.found, p.moved)
        try:
            value = _move_value(base, raw, pid, p)
        except (AttributeError, TypeError, ValueError) as e:
            log.warning("m3u ids: %s left as it was: %s", base, e)
            continue
        if value is None:
            continue
        if remap is not None and p.moved != before[1]:
            settings.setValue(key, json.dumps(value, ensure_ascii=False))
    return p.found, p.moved


# -- at start -----------------------------------------------------------------------------

def is_stable(pid: Any) -> bool:
    """Whether source *pid*'s channels are read with stable ids."""
    return bool(pid) and str(pid) in _STABLE


def start(settings: Settings, sources: Iterable[dict], cache_dir: str | None) -> None:
    """Before the window reads any store: each M3U source moved that can be
    (its map from the last run), and those moved read with stable ids. A
    source with nothing stored by place (new, or all of it synced from a
    computer that moved it) is read with stable ids at once. Never in the
    way of starting."""
    for src in sources:
        if (src or {}).get("kind") != "m3u" or not src.get("id"):
            continue
        pid = str(src["id"])
        flag = FLAG_PREFIX + pid
        try:
            if settings.value(flag, ""):
                _STABLE.add(pid)
                continue
            path = map_path(cache_dir, pid) if cache_dir else ""
            remap = load_map(path) if path else None
            if remap is None:
                found, _moved = migrate(settings, pid, None)
                if found:
                    continue            # moved once its list has been read here
            else:
                found, moved = migrate(settings, pid, remap)
                log.info("m3u ids: %s moved to stable ids (%d of %d values)",
                         pid, moved, found)
                try:
                    os.remove(path)
                except OSError:
                    pass
            settings.setValue(flag, "1")
            _STABLE.add(pid)
        except Exception as e:
            log.warning("m3u ids: %s not moved: %s", pid, e)


def forget(pid: str) -> None:
    """Tests: a source read by place again."""
    _STABLE.discard(str(pid))
