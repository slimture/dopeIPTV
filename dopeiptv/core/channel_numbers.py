"""Nerd: channel numbers of the user's own, per source.

Each source (by its playlist id) keeps a map of channel (stream id) to
number in the app's settings, as JSON under ``nerd_chno_<source id>``. A
number given by hand wins over the one the source sends (a row's ``num``)
everywhere a list is shown or shared, and typing it zaps to that channel.
With Nerd off the source's own numbers are back, untouched: the stored map
is kept for when Nerd is on again.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Protocol

from . import nerd

#: Five digits, as many as a typed number takes.
MAX_NUMBER = 99999
_PREFIX = "nerd_chno_"

# (raw JSON, parsed) per source: a list is decorated on every fetch, and the
# same text needs parsing only once.
_parsed: dict[str, tuple[str, dict[str, int]]] = {}
# Sources whose rows were given a number this run: only those can carry one
# to take back, so every other list is passed through untouched.
_applied: set[str] = set()


class Readable(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


class Settings(Readable, Protocol):
    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


def key(pid: str) -> str:
    """The settings key that holds *pid*'s numbers."""
    return f"{_PREFIX}{pid}"


def valid(number: Any) -> int | None:
    """*number* as a channel number (1 to MAX_NUMBER), else None."""
    if isinstance(number, bool):
        return None
    try:
        n = int(str(number).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= MAX_NUMBER else None


def _parse(pid: str, raw: str) -> dict[str, int]:
    hit = _parsed.get(pid)
    if hit is not None and hit[0] == raw:
        return hit[1]
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    out: dict[str, int] = {}
    if isinstance(data, dict):
        for sid, num in data.items():
            n = valid(num)
            if n is not None and str(sid):
                out[str(sid)] = n
    _parsed[pid] = (raw, out)
    return out


def stored(settings: Readable | None, pid: str | None) -> dict[str, int]:
    """{stream id: number} kept for *pid*, whether Nerd is on or not. The
    dict is shared: copy it before changing it."""
    if settings is None or not pid:
        return {}
    raw = settings.value(key(pid), "")
    return _parse(str(pid), raw if isinstance(raw, str) else "")


def active(pid: str | None) -> dict[str, int]:
    """The numbers in force for *pid*: its own while Nerd is on, none while
    it is off."""
    n = nerd.current()
    if not pid or not n.on:
        return {}
    return stored(n.s, pid)


def _save(settings: Settings, pid: str, numbers: dict[str, int]) -> None:
    raw = json.dumps(numbers, sort_keys=True) if numbers else ""
    settings.setValue(key(pid), raw)
    _parsed[str(pid)] = (raw, dict(numbers))


def get(settings: Readable | None, pid: str | None, stream_id: Any) -> int | None:
    return stored(settings, pid).get(str(stream_id)) if stream_id is not None else None


def holder(numbers: dict[str, int], number: int) -> str | None:
    """The stream id given *number* by hand, if any."""
    return next((sid for sid, n in numbers.items() if n == number), None)


def set_number(settings: Settings, pid: str, stream_id: Any, number: Any) -> str | None:
    """Give a channel *number* (None or 0 clears it). A number is one
    channel's: the channel that had it loses it, and its stream id is
    returned."""
    sid = str(stream_id)
    numbers = dict(stored(settings, pid))
    n = valid(number)
    moved = None
    if n is None:
        numbers.pop(sid, None)
    else:
        moved = holder(numbers, n)
        if moved == sid:
            moved = None
        elif moved is not None:
            numbers.pop(moved, None)
        numbers[sid] = n
    _save(settings, pid, numbers)
    return moved


def clear(settings: Settings, pid: str, stream_id: Any) -> None:
    set_number(settings, pid, stream_id, None)


def clear_all(settings: Settings, pid: str) -> int:
    """Every number of *pid* gone; how many there were."""
    count = len(stored(settings, pid))
    _save(settings, pid, {})
    return count


def number_all(settings: Settings, pid: str, rows: Iterable[dict], keep: bool = True) -> dict[str, int]:
    """1, 2, 3... for *rows* in their order. With *keep* the numbers set by
    hand stay and the rest fill the numbers still free; without, every
    number is new. Returns the numbers now kept."""
    numbers: dict[str, int] = dict(stored(settings, pid)) if keep else {}
    taken = set(numbers.values())
    nxt = 1
    for row in rows:
        sid = row.get("stream_id")
        if sid is None or row.get("_header") or str(sid) in numbers:
            continue
        while nxt in taken:
            nxt += 1
        if nxt > MAX_NUMBER:
            break
        numbers[str(sid)] = nxt
        taken.add(nxt)
        nxt += 1
    _save(settings, pid, numbers)
    return numbers


def own_number(row: dict) -> Any:
    """The number the source gave a row (before any of the user's)."""
    return row.get("_num_own", row.get("num"))


def shown(row: dict, numbers: dict[str, int]) -> Any:
    """The number a row shows: the user's, else the source's."""
    custom = numbers.get(str(row.get("stream_id"))) if numbers else None
    return custom if custom is not None else own_number(row)


def find(rows: Iterable[dict], number: Any, numbers: dict[str, int]) -> dict | None:
    """The channel *number* means: the one given it by hand, else the one
    the source numbers so - but never a channel the user renumbered, and no
    other once the number was given by hand."""
    n = valid(number)
    if n is None:
        return None
    rows = list(rows)
    sid = holder(numbers, n)
    if sid is not None:
        return next((r for r in rows if str(r.get("stream_id")) == sid and not r.get("_header")), None)
    for row in rows:
        rid = row.get("stream_id")
        if rid is None or row.get("_header") or str(rid) in numbers:
            continue
        if valid(own_number(row)) == n:
            return row
    return None


def apply(rows: list[dict], pid: str | None, numbers: dict[str, int] | None = None) -> list[dict]:
    """*rows* (a source's channels) with the user's numbers in ``num``; the
    source's own kept in ``_num_own`` and put back once a number goes (or
    Nerd is off). *numbers*: those in force, else asked of the settings."""
    if numbers is None:
        numbers = active(pid)
    tag = str(pid)
    if not rows or (not numbers and tag not in _applied):
        return rows
    if numbers:
        _applied.add(tag)
    for row in rows:
        sid = row.get("stream_id")
        custom = numbers.get(str(sid)) if (numbers and sid is not None) else None
        if custom is not None:
            if "_num_own" not in row:
                row["_num_own"] = row.get("num")
            row["num"] = custom
        elif "_num_own" in row:
            row["num"] = row.pop("_num_own")
    return rows
