"""XMLTV guide: download, disk cache, and in-memory index for EPG lookups."""

from __future__ import annotations

import io
import pickle
import sys
import threading
import time
import xml.etree.ElementTree as ET
from array import array
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests
from PyQt6.QtCore import QStandardPaths

from .client import XtreamClient


# Bump to invalidate all pickled indexes on disk after a schema change.
# v2: compact column storage (arrays + deduped strings) instead of one
# dict per programme - a ~260 MB guide used to cost ~1 GB of RAM.
_INDEX_VERSION = 2


def normalize_name(s: str | None) -> str:
    """Normalize a channel name for fuzzy matching ("SE: SVT 1 HD" -> "svt1hd")."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def parse_xmltv_time(s: str | None) -> datetime | None:
    """Parse an XMLTV timestamp: YYYYMMDDHHMMSS [+-ZZZZ]."""
    s = (s or "").strip()
    try:
        if len(s) > 14:
            return datetime.strptime(s, "%Y%m%d%H%M%S %z").astimezone()
        return datetime.strptime(s[:14], "%Y%m%d%H%M%S").astimezone()
    except ValueError:
        return None


def epg_cache_path(playlist_id: str | None) -> str:
    """Per-playlist file path for the cached XMLTV guide."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    if not base:
        base = str(Path.home() / ".cache" / "dopeiptv")
    return str(Path(base) / f"epg_{playlist_id or 'default'}.xml")


class XmltvGuide:
    """Downloads and indexes the provider's XMLTV guide.

    Many providers send no EPG via player_api but do have a full schedule
    in XMLTV format.  Once loaded, lookups are pure in-memory dict access
    so the channel list can show "now playing" for every visible row
    without extra network calls.

    The raw XML is cached on disk (per playlist) so a restart shows EPG
    immediately from the previous session.
    """

    CACHE_TTL: int = 6 * 3600
    KEEP_PAST_DAYS: int = 7

    def __init__(self, client: XtreamClient,
                 custom_url: str | None = None,
                 cache_path: str | None = None,
                 progress_cb: Callable[[int], None] | None = None) -> None:
        self.client = client
        self.custom_url = custom_url
        self.cache_path = Path(cache_path) if cache_path else None
        self.progress_cb = progress_cb
        self._lock = threading.Lock()
        self._loaded = False
        self._failed = False
        # Column storage per channel: (starts, stops, titles, descriptions),
        # sorted by start time. Two int64 arrays plus two string tuples cost
        # a fraction of a dict per programme (a guide easily holds 1-2
        # million programmes), and the sorted starts let lookups bisect
        # instead of scanning. Query methods materialise the small
        # {"_plain": True, ...} dicts on demand, so callers see the exact
        # same entries as before.
        self._by_id: dict[str, tuple[array, array, tuple, tuple]] = {}
        self._by_name: dict[str, str] = {}
        self.delay_minutes = 0

    def _now(self) -> float:
        return (datetime.now().astimezone().timestamp()
                - self.delay_minutes * 60)

    def _read_cache(self, max_age: float | None = None) -> bytes | None:
        if not self.cache_path or not self.cache_path.exists():
            return None
        try:
            if (max_age is not None
                    and time.time() - self.cache_path.stat().st_mtime > max_age):
                return None
            return self.cache_path.read_bytes()
        except OSError:
            return None

    def _write_cache(self, data: bytes) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_bytes(data)
        except OSError:
            pass

    def _download(self) -> bytes:
        if self.custom_url:
            r = requests.get(self.custom_url, stream=True, timeout=(20, 300))
        else:
            r = self.client.session.get(
                f"{self.client.server}/xmltv.php",
                params={"username": self.client.username,
                        "password": self.client.password},
                stream=True, timeout=(20, 300))
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        chunks: list[bytes] = []
        received = 0
        for chunk in r.iter_content(64 * 1024):
            chunks.append(chunk)
            received += len(chunk)
            if self.progress_cb:
                self.progress_cb(min(99, received * 100 // total)
                                 if total else -1)
        if self.progress_cb:
            self.progress_cb(100)
        data = b"".join(chunks)
        self._write_cache(data)
        return data

    def ensure_loaded(self, force: bool = False) -> bool:
        """Load the guide if needed.  Returns True when data is available."""
        with self._lock:
            if self._loaded and not force:
                return True
            if self._failed and not force:
                return False
            data = None if force else self._read_cache(max_age=self.CACHE_TTL)
            source = "cache"
            if data is None:
                stale = None if force else self._read_cache(max_age=None)
                if stale is not None and not self._loaded:
                    try:
                        self._parse(stale)
                        self._loaded = True
                    except Exception:
                        pass
                try:
                    data = self._download()
                    source = "download"
                except Exception:
                    data = stale
                    source = "stale cache (download failed)"
            if data is None:
                self._failed = True
                return False
            try:
                # Prefer the pickled parse-index next to the XML: a 280 MB
                # XMLTV dump takes 10-15 s to iterparse but loads from
                # pickle in ~1 s. Fall back to re-parsing (and re-writing
                # the pickle) if it's missing, older than the XML, or
                # from an older schema version.
                if source == "cache" and self._load_index():
                    pass
                else:
                    self._parse(data)
                    self._write_index()
                self._loaded = True
                self._failed = False
                print(f"[dopeIPTV] EPG loaded from {source} "
                      f"({len(data) // 1024} KB)", file=sys.stderr)
            except Exception:
                self._failed = self._loaded is False
            return self._loaded

    # -- pickle index --------------------------------------------------------

    def _index_path(self) -> Path | None:
        if not self.cache_path:
            return None
        return self.cache_path.with_suffix(self.cache_path.suffix + ".pkl")

    def _load_index(self) -> bool:
        p = self._index_path()
        if not p or not p.exists() or not self.cache_path:
            return False
        try:
            if p.stat().st_mtime < self.cache_path.stat().st_mtime:
                return False
            with p.open("rb") as f:
                blob = pickle.load(f)
        except (OSError, pickle.PickleError, EOFError, ValueError):
            return False
        if not isinstance(blob, dict) or blob.get("v") != _INDEX_VERSION:
            return False
        self._by_id = blob.get("by_id", {})
        self._by_name = blob.get("by_name", {})
        return True

    def _write_index(self) -> None:
        p = self._index_path()
        if not p:
            return
        try:
            with p.open("wb") as f:
                pickle.dump({"v": _INDEX_VERSION,
                             "by_id": self._by_id,
                             "by_name": self._by_name},
                            f, protocol=pickle.HIGHEST_PROTOCOL)
        except OSError:
            pass

    def _sched_for(self, item: dict) -> tuple[array, array, tuple, tuple] | None:
        cid = (item.get("epg_channel_id") or "").strip().lower()
        sched = self._by_id.get(cid)
        if sched is None:
            cid = self._by_name.get(normalize_name(item.get("name")))
            sched = self._by_id.get(cid)
        return sched

    @staticmethod
    def _entry(sched: tuple, i: int) -> dict:
        starts, stops, titles, descs = sched
        return {"_plain": True,
                "title": titles[i],
                "description": descs[i],
                "start_timestamp": starts[i],
                "stop_timestamp": stops[i]}

    def _entries_for(self, item: dict) -> list[dict]:
        sched = self._sched_for(item)
        if not sched:
            return []
        return [self._entry(sched, i) for i in range(len(sched[0]))]

    # No broadcast programme runs longer than a day; bounding the backward
    # scan by this keeps lookups O(one day of entries) instead of O(all).
    _MAX_PROG_SECS = 86400

    def listings_for(self, item: dict, limit: int = 8) -> list[dict]:
        if not self.ensure_loaded():
            return []
        sched = self._sched_for(item)
        if not sched:
            return []
        now = self._now()
        starts, stops = sched[0], sched[1]
        n = len(starts)
        lo = bisect_right(starts, now)
        # Everything from lo on starts in the future (so ends there too);
        # additionally, anything that started within the last day may still
        # be airing. Collect in start order, exactly like the old full scan.
        out: list[dict] = []
        j = bisect_left(starts, now - self._MAX_PROG_SECS)
        while j < n and len(out) < limit:
            if stops[j] > now:
                out.append(self._entry(sched, j))
            j += 1
        return out

    def now_for(self, item: dict) -> tuple[str, float] | None:
        """(title, percent) for the currently airing programme, or None."""
        p = self.current_programme(item)
        if p is None:
            return None
        length = p["stop_timestamp"] - p["start_timestamp"]
        now = self._now()
        pct = (now - p["start_timestamp"]) / length * 100 if length else 0
        return p["title"], pct

    def current_programme(self, item: dict) -> dict | None:
        """The full entry for the currently airing programme, or None."""
        if not self._loaded:
            return None
        sched = self._sched_for(item)
        if not sched:
            return None
        now = self._now()
        starts, stops = sched[0], sched[1]
        # The airing programme started within the last day (see
        # _MAX_PROG_SECS); return the earliest such entry covering now,
        # matching the old first-match-in-start-order scan.
        lo = bisect_left(starts, now - self._MAX_PROG_SECS)
        hi = bisect_right(starts, now)
        for j in range(lo, hi):
            if starts[j] <= now < stops[j]:
                return self._entry(sched, j)
        return None

    def past_programmes(self, item: dict, days: int) -> list[dict]:
        """Past programmes (newest first), at most *days* back."""
        if not self.ensure_loaded():
            return []
        sched = self._sched_for(item)
        if not sched:
            return []
        now = self._now()
        cutoff = now - days * 86400
        starts, stops = sched[0], sched[1]
        lo = bisect_left(starts, cutoff)
        out = [self._entry(sched, i)
               for i in range(lo, len(starts))
               if stops[i] <= now and starts[i] >= cutoff]
        out.reverse()
        return out

    def _parse(self, data: bytes) -> None:
        cutoff = (datetime.now().astimezone().timestamp()
                  - self.KEEP_PAST_DAYS * 86400)
        rows: dict[str, list[tuple]] = {}
        by_name: dict[str, str] = {}
        seen: set[tuple] = set()
        # Guides repeat the same titles and descriptions endlessly (reruns,
        # daily shows, episode blurbs); keeping one str object per unique
        # text instead of one per programme cuts the string heap by well
        # over half - and shrinks the pickle index the same way.
        text_pool: dict[str, str] = {}

        def pooled(s: str) -> str:
            return text_pool.setdefault(s, s)

        for _, el in ET.iterparse(io.BytesIO(data)):
            if el.tag == "channel":
                cid = (el.get("id") or "").strip().lower()
                if cid:
                    for dn in el.findall("display-name"):
                        name = normalize_name(dn.text)
                        if name:
                            by_name.setdefault(name, cid)
                el.clear()
            elif el.tag == "programme":
                cid = (el.get("channel") or "").strip().lower()
                start = parse_xmltv_time(el.get("start"))
                stop = parse_xmltv_time(el.get("stop"))
                title = (el.findtext("title") or "").strip()
                if cid and start and stop and stop.timestamp() > cutoff:
                    dedup_key = (cid, int(start.timestamp()), title.lower())
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        rows.setdefault(cid, []).append((
                            int(start.timestamp()),
                            int(stop.timestamp()),
                            pooled(title),
                            pooled((el.findtext("desc") or "").strip()),
                        ))
                el.clear()
        del seen, text_pool
        by_id: dict[str, tuple[array, array, tuple, tuple]] = {}
        for cid, lst in rows.items():
            lst.sort(key=lambda r: r[0])
            by_id[cid] = (array("q", (r[0] for r in lst)),
                          array("q", (r[1] for r in lst)),
                          tuple(r[2] for r in lst),
                          tuple(r[3] for r in lst))
        self._by_id, self._by_name = by_id, by_name
