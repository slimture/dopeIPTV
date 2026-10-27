"""XMLTV guide: download, disk cache, and in-memory index for EPG lookups."""

from __future__ import annotations

import gzip
import io
import os
import pickle
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from array import array
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..core._lazy_requests import requests
from PyQt6.QtCore import QStandardPaths

from ..core.log import log
from .client import XtreamClient


# Bump to invalidate all pickled indexes on disk after a schema change.
# v2: compact column storage (arrays + deduped strings) instead of one
# dict per programme - a ~260 MB guide used to cost ~1 GB of RAM.
# v3: a header, then the channels in pieces (see index_blob).
_INDEX_VERSION = 3
_CHANNELS_PER_PIECE = 50

# The command line that makes the app a guide parser (see index_main).
INDEX_FLAG = "--epg-index"

Schedule = tuple[array, array, tuple, tuple]

# zlib's one-shot calls and pickle.loads hold the interpreter for their
# whole run - unpacking a 140 MB guide in one call stopped the window, and
# the player's frames with it, for 0.8 s. Fed a slice at a time, the window
# runs between slices.
_ZLIB_STEP = 1 << 18


def gzip_gently(data: bytes, level: int = 5) -> bytes:
    """gzip.compress(), a slice at a time."""
    c = zlib.compressobj(level, zlib.DEFLATED, 31)
    view = memoryview(data)
    out = [c.compress(view[i:i + _ZLIB_STEP])
           for i in range(0, len(view), _ZLIB_STEP)]
    out.append(c.flush())
    return b"".join(out)


def gunzip_gently(data: bytes) -> bytes:
    """gzip.decompress(), a slice at a time, every member of the stream.
    Raises zlib.error on damaged or cut-short data."""
    out: list[bytes] = []
    view = memoryview(data)
    while len(view):
        d = zlib.decompressobj(31)
        pos = 0
        while not d.eof:
            if pos >= len(view):
                raise zlib.error("compressed data ends early")
            out.append(d.decompress(view[pos:pos + _ZLIB_STEP]))
            pos += _ZLIB_STEP
        rest = d.unused_data + bytes(view[pos:])
        if rest[:2] != b"\x1f\x8b":
            break           # padding after the last member
        view = memoryview(rest)
    return b"".join(out)


def normalize_name(s: str | None) -> str:
    """Normalize a channel name for fuzzy matching ("SE: SVT 1 HD" -> "svt1hd")."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def parse_xmltv_time(s: str | None) -> datetime | None:
    """Parse an XMLTV timestamp: YYYYMMDDHHMMSS [+-ZZZZ].

    The offset often comes without the space ("20260927200000+0200"); the
    strict format read that as no time at all, and a guide written that way
    came out empty."""
    s = (s or "").strip()
    rest = s[14:].strip()
    try:
        dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S")
        if rest.upper() in ("Z", "GMT", "UTC"):
            rest = "+0000"
        if rest:
            dt = dt.replace(tzinfo=datetime.strptime(rest, "%z").tzinfo)
        return dt.astimezone()
    except (ValueError, OverflowError, OSError):
        return None


def _epg_cache_dir() -> "Path":
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    if not base:
        base = str(Path.home() / ".cache" / "dopeiptv")
    return Path(base)


def epg_cache_path(playlist_id: str | None) -> str:
    """Per-playlist file path for the cached XMLTV guide."""
    return str(_epg_cache_dir() / f"epg_{playlist_id or 'default'}.xml")


def prune_epg_caches(keep_ids) -> int:
    """Delete cached EPG guides (compressed XMLTV + parsed .pkl index) for
    playlists that no longer exist. Each playlist caches its whole guide -
    often hundreds of MB - under epg_<id>.xml, and removing a playlist never
    deleted its cache, so over time they pile up into gigabytes. Keeps the
    guides for *keep_ids* (plus the 'default' no-playlist cache); returns how
    many files were removed. Safe to call at startup."""
    d = _epg_cache_dir()
    if not d.exists():
        return 0
    keep = {str(i) for i in keep_ids if i} | {"default"}
    removed = 0
    for f in d.glob("epg_*.xml*"):
        name = f.name[4:]  # drop the "epg_" prefix
        for suffix in (".xml.pkl", ".xml"):
            if name.endswith(suffix):
                pid = name[:-len(suffix)]
                break
        else:
            continue
        if pid not in keep:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    # The provider's lists, kept here too (make_client): the folder with a
    # file per list, and the single file older versions wrote.
    for f in d.glob("lists_*"):
        for suffix in (".json", ".d"):
            if f.name.endswith(suffix):
                pid = f.name[len("lists_"):-len(suffix)]
                break
        else:
            continue
        if pid not in keep:
            try:
                if f.is_dir():
                    shutil.rmtree(f)
                else:
                    f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def parse_xmltv(data: bytes, keep_past_days: int
                ) -> tuple[dict[str, Schedule], dict[str, str]]:
    """XMLTV (plain or gzipped) -> (schedules by channel id, channel id by
    normalised display name)."""
    # Many EPG URLs serve gzipped XMLTV (.xml.gz); requests doesn't
    # transparently decompress a gzip *body* (only transfer-encoding), so
    # detect the gzip magic and inflate it ourselves.
    if data[:2] == b"\x1f\x8b":
        try:
            data = gunzip_gently(data)
        except zlib.error:
            pass
    # Robustness: refuse a guide that declares its own internal XML
    # entities. A hostile or broken EPG URL could otherwise ship a
    # "billion laughs" entity-expansion bomb that balloons memory during
    # iterparse. Entity definitions live in the DOCTYPE prolog, before the
    # root element, so scanning the head is enough - and real XMLTV never
    # declares custom entities (a plain <!DOCTYPE tv SYSTEM "xmltv.dtd">
    # carries none and is left to parse normally). Caught by ensure_loaded.
    if b"<!ENTITY" in data[:65536]:
        raise ValueError("XMLTV declares internal entities; refusing to parse")
    cutoff = (datetime.now().astimezone().timestamp()
              - keep_past_days * 86400)
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
    seen.clear()
    text_pool.clear()
    by_id: dict[str, Schedule] = {}
    for cid, lst in rows.items():
        lst.sort(key=lambda r: r[0])
        by_id[cid] = (array("q", (r[0] for r in lst)),
                      array("q", (r[1] for r in lst)),
                      tuple(r[2] for r in lst),
                      tuple(r[3] for r in lst))
    return by_id, by_name


def index_blob(by_id: dict[str, Schedule], by_name: dict[str, str]) -> bytes:
    """The index as stored next to the cached guide, gzipped: a header,
    then the channels in pieces, one pickle after another, so reading it
    back never holds the interpreter for the whole guide at once. One
    pickler for all of them keeps a text shared by channels in different
    pieces shared after loading."""
    items = list(by_id.items())
    pieces = range(0, len(items), _CHANNELS_PER_PIECE)
    buf = io.BytesIO()
    p = pickle.Pickler(buf, protocol=pickle.HIGHEST_PROTOCOL)
    p.dump({"v": _INDEX_VERSION, "pieces": len(pieces), "by_name": by_name})
    for i in pieces:
        p.dump(dict(items[i:i + _CHANNELS_PER_PIECE]))
    return gzip_gently(buf.getvalue())


def read_index_blob(data: bytes
                    ) -> tuple[dict[str, Schedule], dict[str, str]] | None:
    """(by_id, by_name) from index_blob()'s bytes; None when damaged or from
    another schema version."""
    try:
        # Index files are gzip-compressed; a plain pickle (proto starts \x80,
        # never the gzip magic) loads as well. Unpacked as it is read, the
        # whole of it never sits in memory at once.
        f: io.BufferedIOBase = io.BytesIO(data)
        if data[:2] == b"\x1f\x8b":
            f = gzip.GzipFile(fileobj=f)
        u = pickle.Unpickler(f)
        head = u.load()
        if not isinstance(head, dict) or head.get("v") != _INDEX_VERSION:
            return None
        by_id: dict[str, Schedule] = {}
        for _ in range(head["pieces"]):
            by_id.update(u.load())
        if f.read(1):       # the end of the file checks gzip's CRC too
            return None
    except Exception:
        # Any damage at all means "parse the XML again". Letting one
        # through (zlib.error, a truncated pickle's AttributeError...)
        # failed the whole guide load instead, and on every start.
        return None
    return by_id, head.get("by_name", {})


def _write_atomic(path: Path, data: bytes) -> None:
    """Write *path* whole or not at all: a write cut short (app killed,
    disk full) leaves the old file, never a truncated one."""
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def index_main(argv: list[str] | None = None) -> int:
    """The app started with INDEX_FLAG: XMLTV on stdin, its index_blob() on
    stdout. The window runs this as a process of its own (_parse_elsewhere)
    so a big guide's parse never competes with it for the interpreter.
    ``--cache PATH`` also keeps the guide there, gzipped, as the window's
    cache - packing 140 MB stalled the window too."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        chunks = []
        while True:
            chunk = os.read(0, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        if "--cache" in args:
            cache = Path(args[args.index("--cache") + 1])
            cache.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(cache, data if data[:2] == b"\x1f\x8b"
                          else gzip_gently(data))
        by_id, by_name = parse_xmltv(data, XmltvGuide.KEEP_PAST_DAYS)
        out = memoryview(index_blob(by_id, by_name))
        while out:
            out = out[os.write(1, out):]
    except Exception as e:
        try:
            os.write(2, f"epg index: {type(e).__name__}: {e}\n".encode())
        except OSError:
            pass
        return 1
    return 0


def _index_command() -> tuple[list[str], dict[str, str]]:
    """How to start index_main(): the frozen app is its own interpreter
    (dopeiptv.py hands INDEX_FLAG to index_main); from source it is
    `python -m`, with this package findable from any directory."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        return [sys.executable, INDEX_FLAG], env
    root = str(Path(__file__).resolve().parent.parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (root, env.get("PYTHONPATH", "")) if p)
    return [sys.executable, "-m", "dopeiptv.providers.epg_index",
            INDEX_FLAG], env


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
    # Parse in a process of its own (see _parse_elsewhere); off, or when
    # that process cannot run, the parse happens in the calling thread.
    PARSE_ELSEWHERE: bool = True
    PARSE_TIMEOUT_S: float = 900.0

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
        self._by_id: dict[str, Schedule] = {}
        self._by_name: dict[str, str] = {}
        self.delay_minutes = 0

    def _now(self) -> float:
        return (datetime.now().astimezone().timestamp()
                - self.delay_minutes * 60)

    def _cache_age(self) -> float | None:
        """Seconds since the cached guide was written; None without one."""
        try:
            if self.cache_path is not None:
                return time.time() - self.cache_path.stat().st_mtime
        except OSError:
            pass
        return None

    def _read_cache(self, max_age: float | None = None) -> bytes | None:
        age = self._cache_age()
        if age is None or (max_age is not None and age > max_age):
            return None
        try:
            data = self.cache_path.read_bytes() if self.cache_path else b""
        except OSError:
            return None
        # Transparently gunzip. New caches are gzip-compressed (a raw XMLTV
        # guide shrinks ~10x - hundreds of MB down to tens), so the on-disk
        # cache and the startup read stay small. A plain-XML cache written by
        # an older build (no gzip magic) is returned unchanged.
        if data[:2] == b"\x1f\x8b":
            try:
                return gunzip_gently(data)
            except Exception:
                # zlib.error is not an OSError: a damaged cache raised out of
                # ensure_loaded before the download that would replace it,
                # and the guide stayed broken until the cache was cleared.
                return None
        return data or None

    _write_atomic = staticmethod(_write_atomic)

    def _write_cache(self, data: bytes) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_atomic(self.cache_path,
                               data if data[:2] == b"\x1f\x8b"
                               else gzip_gently(data))
        except OSError:
            pass

    def clear_cache(self) -> None:
        """Delete the on-disk EPG cache (compressed guide + parsed index) and
        forget the loaded guide, so the next load re-fetches from the provider.
        Backs Settings' 'Clear EPG cache' - useful when a guide went stale or
        a provider changed its XMLTV."""
        for p in (self.cache_path, self._index_path()):
            try:
                if p is not None and p.exists():
                    p.unlink()
            except OSError:
                pass
        with self._lock:
            self._by_id = {}
            self._by_name = {}
            self._loaded = False
            self._failed = False

    def _effective_url(self) -> str:
        """The XMLTV URL to fetch: an explicit per-playlist URL wins, else the
        one an M3U playlist advertises in its #EXTM3U header (url-tvg). The M3U
        client only knows that after it has parsed, so ensure it is loaded and
        read the hint lazily here rather than at construction time."""
        if self.custom_url:
            return self.custom_url
        if hasattr(self.client, "epg_url"):
            ensure = getattr(self.client, "_ensure", None)
            if callable(ensure):
                try:
                    ensure()
                except Exception:
                    pass
            return getattr(self.client, "epg_url", "") or ""
        return ""

    def _download(self) -> bytes:
        url = self._effective_url()
        if url:
            r = requests.get(url, stream=True, timeout=(20, 300))
        elif hasattr(self.client, "epg_url"):
            # An M3U/offline client with no guide URL: there is no Xtream
            # xmltv.php endpoint to fall back to, so report "no EPG" cleanly.
            raise RuntimeError("no EPG source for this playlist")
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
        # Reported when the figure changes, and without a total twice a
        # second: once per 64 KB it was ~2,200 calls into the window for a
        # 140 MB guide, each one restyling the loading strip.
        said, said_at = None, 0.0
        for chunk in r.iter_content(64 * 1024):
            chunks.append(chunk)
            received += len(chunk)
            if self.progress_cb:
                pct = min(99, received * 100 // total) if total else -1
                now = time.monotonic()
                if pct != said or (pct < 0 and now - said_at >= 0.5):
                    said, said_at = pct, now
                    self.progress_cb(pct)
        if self.progress_cb:
            self.progress_cb(100)
        # Not cached here: the parse process keeps it (_parse_and_index).
        return b"".join(chunks)

    def is_loaded(self) -> bool:
        """Non-blocking: whether guide data is available right now. Lets the
        UI (e.g. the EPG grid) tell 'no guide data for this channel' apart
        from 'the guide simply hasn't finished loading yet'."""
        return self._loaded

    def ensure_loaded(self, force: bool = False) -> bool:
        """Load the guide if needed.  Returns True when data is available."""
        # A guide in memory answers at once, also while a refresh holds the
        # lock for its download and parse: waiting for it here tied up every
        # pool thread that wanted a schedule (the detail pane, recordings,
        # search), and logos and lists queued behind them.
        if self._loaded and not force:
            return True
        with self._lock:
            if self._loaded and not force:
                return True
            if self._failed and not force:
                return False
            age = self._cache_age()
            if (age is not None and not self._loaded
                    and self._load_from_disk()):
                # A stale guide - or the one a refresh replaces - stands in
                # while the new one downloads.
                if not force and age <= self.CACHE_TTL:
                    log.info("EPG loaded from cache")
                    return True
            try:
                data = self._download()
            except Exception as e:
                log.info("EPG download failed (%s)", e)
                # A refresh that cannot reach the guide keeps the one it has
                # (the copy on disk, whatever its age) rather than none.
                self._failed = not self._loaded
                return self._loaded
            try:
                self._parse_and_index(data, keep=True)
                self._loaded = True
                self._failed = False
                log.info("EPG loaded from download (%d KB)", len(data) // 1024)
            except Exception:
                self._failed = not self._loaded
            return self._loaded

    def _load_from_disk(self) -> bool:
        """The guide on disk into memory. Its index alone does it - the XML
        beside it is unpacked and parsed again only when the index is
        missing, damaged or older than the XML. A 280 MB guide parses in
        10-25 s and its index loads in about one."""
        try:
            if not self._load_index():
                data = self._read_cache()
                if data is None:
                    return False
                self._parse_and_index(data)
        except Exception:
            return False
        self._loaded = True
        self._failed = False
        return True

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
            data = p.read_bytes()
        except OSError:
            return False
        loaded = read_index_blob(data)
        if loaded is None:
            return False
        self._by_id, self._by_name = loaded
        return True

    def _write_index(self) -> None:
        p = self._index_path()
        if not p:
            return
        try:
            self._write_atomic(p, index_blob(self._by_id, self._by_name))
        except OSError:
            pass

    def _parse_and_index(self, data: bytes, keep: bool = False) -> None:
        """Parse *data* into the guide and keep its index on disk - and,
        with *keep* (a fresh download), the guide itself as the cache."""
        cache = self.cache_path if keep else None
        if not (self.PARSE_ELSEWHERE and self._parse_elsewhere(data, cache)):
            if keep:
                self._write_cache(data)
            self._parse(data)
            self._write_index()

    def _parse_elsewhere(self, data: bytes, cache: Path | None = None) -> bool:
        """Parse in a process of its own and take its index. A parse in a
        thread holds the interpreter (the GIL) for most of its 10-25 s on a
        big guide, and the window shares the rest with it: the whole app
        crawled until the guide was in. With *cache* that process also
        keeps the guide there. False when it cannot run or fails; the
        caller then parses here, as before."""
        cmd, env = _index_command()
        if cache is not None:
            cmd = [*cmd, "--cache", str(cache)]
        try:
            r = subprocess.run(
                cmd, input=data, capture_output=True, env=env,
                timeout=self.PARSE_TIMEOUT_S,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               if sys.platform == "win32" else 0))
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            log.info("EPG: no parse process (%s) - parsing in the app", e)
            return False
        loaded = read_index_blob(r.stdout) if r.returncode == 0 else None
        if loaded is None:
            err = (r.stderr or b"")[-300:].decode("utf-8", "replace").strip()
            log.info("EPG: parse process failed (exit %s: %s) - parsing in "
                     "the app", r.returncode, err)
            return False
        self._by_id, self._by_name = loaded
        p = self._index_path()
        if p is not None:
            try:
                self._write_atomic(p, r.stdout)
            except OSError:
                pass
        return True

    def _sched_for(self, item: dict) -> tuple[array, array, tuple, tuple] | None:
        cid = (item.get("epg_channel_id") or "").strip().lower()
        sched = self._by_id.get(cid)
        if sched is None:
            name_cid = self._by_name.get(normalize_name(item.get("name")))
            if name_cid is not None:
                sched = self._by_id.get(name_cid)
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
        # Anything that started within the last day may still be airing;
        # collect in start order, exactly like the old full scan.
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

    def programmes_in(self, item: dict, win_start: float,
                      win_stop: float) -> list[dict]:
        """Every programme overlapping the window [win_start, win_stop],
        in air order - for the timeline grid."""
        if not self._loaded:
            return []
        sched = self._sched_for(item)
        if not sched:
            return []
        starts, stops = sched[0], sched[1]
        n = len(starts)
        out: list[dict] = []
        j = bisect_left(starts, win_start - self._MAX_PROG_SECS)
        while j < n and starts[j] < win_stop:
            if stops[j] > win_start:
                out.append(self._entry(sched, j))
            j += 1
        return out

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

    def search(self, items, query: str, win_start: float, win_stop: float,
               limit: int = 300) -> list[dict]:
        """Find programmes whose title or description contains *query*
        (case-insensitive) airing within [win_start, win_stop], across the
        given channel *items*. Each result carries its channel item under
        ``_channel`` so callers can tune in or set a reminder. Sorted by start
        time and capped at *limit*."""
        if not self.ensure_loaded():
            return []
        q = (query or "").strip().lower()
        if not q:
            return []
        out: list[dict] = []
        seen: set = set()
        for item in items:
            sched = self._sched_for(item)
            if not sched:
                continue
            starts, stops, titles, descs = sched
            n = len(starts)
            j = bisect_left(starts, win_start - self._MAX_PROG_SECS)
            while j < n and starts[j] < win_stop:
                if stops[j] > win_start and (
                        q in titles[j].lower() or q in descs[j].lower()):
                    # A channel can appear more than once in the line-up; key on
                    # (name, start, title) so the same airing isn't listed twice.
                    dedup = (item.get("name"), starts[j], titles[j])
                    if dedup not in seen:
                        seen.add(dedup)
                        e = self._entry(sched, j)
                        e["_channel"] = item
                        out.append(e)
                j += 1
        out.sort(key=lambda p: p["start_timestamp"])
        return out[:limit]

    def _parse(self, data: bytes) -> None:
        self._by_id, self._by_name = parse_xmltv(data, self.KEEP_PAST_DAYS)
