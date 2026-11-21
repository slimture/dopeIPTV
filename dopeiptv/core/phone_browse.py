"""The phone remote's lists beyond the channels, pure: what the page
(core/phone_page.py) browses - films, series and their episodes, Recent
(Continue watching and History), local files, radio and recordings -
as ui/mw_phone_browse.py fills them on the server's thread.

- ``Lists``: a list is built once per key and version - on the server's
  thread, one build at a time per key however many phones ask - and kept
  ``CACHE_SECS`` (the series page's ten minutes), the oldest let go past
  ``CACHE_MAX``.
- ``paged``: an answer is a page of ``PAGE`` rows and where the next
  starts - never a list of twenty thousand rows at once.
- ``Refs``: a row the phone may play or show a picture of is handed out as
  an opaque id; only what was listed can be played (a local file's path,
  a station's address never come from the phone).
- ``episodes``: a series' seasons (Specials last, core/series_view.py) and
  each episode's line - number, title, length, watched, how far - as the
  Episodes view shows them.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, TypeVar

from . import series_view as sv
from .palette_match import fold, words

#: Rows in one answer.
PAGE = 60
#: How long a built list is kept, and how many are.
CACHE_SECS = 10 * 60.0
CACHE_MAX = 64
#: Ids handed out at most (the oldest forgotten first).
REFS_MAX = 20_000
#: What the page may ask: a path, a search, where a page starts.
MAX_PATH = 400
MAX_QUERY = 80
MAX_OFFSET = 1_000_000

_REF = re.compile(r"[a-z][0-9a-f]{15}")

T = TypeVar("T")


def clean_path(raw: Any) -> str:
    """A list's path as the page asks it ("vod/12", "local/<id>"): plain
    and short, or ""."""
    text = str(raw or "").strip().strip("/")
    if len(text) > MAX_PATH or not re.fullmatch(r"[A-Za-z0-9_./:-]*", text) or ".." in text:
        return ""
    return text


def query_of(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:MAX_QUERY]


def offset_of(raw: Any) -> int:
    try:
        return max(0, min(MAX_OFFSET, int(str(raw or "0"))))
    except ValueError:
        return 0


def ref_of(raw: Any) -> str:
    """An id the page hands back, when it looks like one of ours."""
    text = str(raw or "").strip()
    return text if _REF.fullmatch(text) else ""


def paged(rows: Sequence[dict], offset: int, size: int = PAGE) -> dict[str, Any]:
    """One page of *rows* from *offset*: the rows, how many there are in
    all, and where the next page starts (None at the end)."""
    start = max(0, min(int(offset), len(rows)))
    end = start + max(1, int(size))
    return {"items": list(rows[start:end]), "total": len(rows), "offset": start,
            "next": end if end < len(rows) else None}


def matching(rows: Iterable[dict], query: str, field: str = "name") -> list[dict]:
    """*rows* whose *field* holds every word of *query* (case, accents and
    punctuation left out, as the command palette matches)."""
    terms = words(query)
    if not terms:
        return list(rows)
    out = []
    for r in rows:
        name = fold(str(r.get(field) or ""))
        if all(t in name for t in terms):
            out.append(r)
    return out


def percent(pos: Any, dur: Any) -> int:
    """How far a title got, 0-100 (0: not started or not known)."""
    try:
        p, d = float(pos or 0), float(dur or 0)
    except (TypeError, ValueError):
        return 0
    if d <= 0 or p <= 0:
        return 0
    return max(1, min(100, int(round(100 * p / d))))


class Lists:
    """Lists built on the server's thread, kept by key and version."""

    def __init__(self, ttl: float = CACHE_SECS, cap: int = CACHE_MAX,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self.cap, self.clock = ttl, cap, clock
        self._lock = threading.Lock()
        self._kept: OrderedDict[tuple, tuple[Any, float, Any]] = OrderedDict()
        self._building: dict[tuple, threading.Lock] = {}
        self.builds = 0

    def get(self, key: tuple, version: Any, build: Callable[[], T]) -> tuple[T, bool]:
        """(the list for *key* at *version*, whether it was built now)."""
        hit = self._hit(key, version)
        if hit is not None:
            return hit, False
        with self._lock:
            gate = self._building.setdefault(key, threading.Lock())
        with gate:
            hit = self._hit(key, version)
            if hit is not None:
                return hit, False
            value = build()
            with self._lock:
                self.builds += 1
                self._kept.pop(key, None)
                self._kept[key] = (version, self.clock(), value)
                while len(self._kept) > self.cap:
                    old, _ = self._kept.popitem(last=False)
                    self._building.pop(old, None)
            return value, True

    def peek(self, key: tuple, version: Any) -> Any:
        return self._hit(key, version)

    def _hit(self, key: tuple, version: Any) -> Any:
        with self._lock:
            got = self._kept.get(key)
            if got is None or got[0] != version or self.clock() - got[1] > self.ttl:
                return None
            self._kept.move_to_end(key)
            return got[2]

    def clear(self) -> None:
        with self._lock:
            self._kept.clear()


class Refs:
    """Opaque ids for what the phone may play or show the picture of: the
    same thing listed again gets the same id."""

    def __init__(self, cap: int = REFS_MAX) -> None:
        self.cap = cap
        self._lock = threading.Lock()
        self._ids: OrderedDict[str, tuple[str, Any]] = OrderedDict()

    def put(self, kind: str, ident: Any, value: Any) -> str:
        raw = f"{kind}\x00{ident}".encode("utf-8", "replace")
        ref = kind[:1].lower() + hashlib.sha256(raw).hexdigest()[:15]
        with self._lock:
            self._ids.pop(ref, None)
            self._ids[ref] = (kind, value)
            while len(self._ids) > self.cap:
                self._ids.popitem(last=False)
        return ref

    def get(self, ref: str) -> tuple[str, Any] | None:
        with self._lock:
            return self._ids.get(ref)

    def clear(self) -> None:
        with self._lock:
            self._ids.clear()


def episodes(seasons: Sequence[sv.Season], series_name: str,
             labels: Sequence[str], short: Callable[[dict], str],
             states: Mapping[str, tuple[bool, float, float]],
             ref: Callable[[dict], str]) -> list[dict[str, Any]]:
    """The Episodes view's seasons: each with its chip's label and its
    episodes as compact lines - the id to play it by, "E5", the title,
    the length in minutes, watched, how far (0-100). *states*: an
    episode's key -> (watched, position, length) as the window has them."""
    out: list[dict[str, Any]] = []
    for season, label in zip(seasons, labels, strict=False):
        rows = []
        for ep in season.episodes:
            key = sv.episode_key(ep)
            watched, pos, dur = states.get(key, (False, 0.0, 0.0))
            minutes = sv.minutes_of(ep) or (int(round(dur / 60)) if dur > 0 else 0)
            rows.append({"ref": ref(ep), "key": key, "n": short(ep),
                         "title": sv.episode_title(ep, series_name), "min": minutes,
                         "watched": bool(watched),
                         "pct": 0 if watched else percent(pos, dur if dur > 0 else minutes * 60)})
        out.append({"label": label, "specials": season.specials, "episodes": rows})
    return out


def opening(seasons: Sequence[dict], playing: str, next_key: str) -> int:
    """The season the view opens on: the playing episode's, else the next
    one's, else the first."""
    for want in (playing, next_key):
        if not want:
            continue
        for i, s in enumerate(seasons):
            if any(e.get("key") == want for e in s.get("episodes") or []):
                return i
    return 0
