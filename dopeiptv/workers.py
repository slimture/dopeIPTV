"""Thread-pool workers and asynchronous logo loader."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

# DOPEIPTV_IMG_DEBUG=1 traces every image decision (RAM/disk/network
# hit, HTTP status, decode failure, dead-marking) to stderr - the
# support tool for "covers aren't loading" reports.
_IMG_DEBUG = bool(os.environ.get("DOPEIPTV_IMG_DEBUG"))

# A browser-style User-Agent for image fetches. python-requests'
# default UA gets rejected outright by several image hosts the IPTV
# world leans on: Wikipedia returns 403 for it, and some Xtream
# panels (ptv.is et al) TCP-reset the connection - which looked like
# the provider being down when other IPTV apps loaded the same URLs
# fine. They send a real UA; so do we.
_IMG_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
                   "dopeIPTV"),
    "Accept": "image/*,*/*;q=0.8",
}


def _img_dbg(msg: str) -> None:
    if _IMG_DEBUG:
        print(f"[dopeIPTV:img] {msg}", file=sys.stderr, flush=True)


# A TMDB poster/backdrop path embedded in a provider image URL. Many
# Xtream panels proxy TMDB art under their own host, e.g.
#   http://panel:2095/images/movies/<tmdb_token>.jpg
#   http://panel:2095/images/<tmdb_token>_small.jpg
# but the proxy frequently 404s (or the host is down) while
# image.tmdb.org serves the real file. Detect the token and go to the
# source. TMDB tokens are ~27-char base62 strings; the guards below
# keep provider-native ids from being mis-rewritten:
#   * MD5 upload ids are 32 lowercase-hex chars (no uppercase)
#   * amazon/IMDB 'MV5B...' segments carry ',', '@@' and '_V1_' which
#     break the contiguous token
# The regex therefore requires the token to sit right before the
# extension (with an optional _size suffix), and tmdb_url_from_provider
# additionally requires mixed case.
_TMDB_EMBED_RE = re.compile(
    r"/([A-Za-z0-9]{26,32})(?:_[A-Za-z]+)?\.(jpe?g|png)(?:$|\?)", re.I)


def tmdb_url_from_provider(url: str | None) -> str | None:
    """If *url* is a provider image URL that embeds a TMDB poster path,
    return the direct image.tmdb.org URL for it; otherwise None."""
    if not url or "image.tmdb.org" in url:
        return None
    m = _TMDB_EMBED_RE.search(url)
    if not m:
        return None
    token, ext = m.group(1), m.group(2).lower()
    # Real TMDB tokens mix upper and lower case. All-lowercase (MD5
    # hex) or all-uppercase are provider-native ids, not TMDB paths.
    if not (any(c.isupper() for c in token)
            and any(c.islower() for c in token)):
        return None
    return f"https://image.tmdb.org/t/p/w500/{token}.{ext}"

import requests
from PyQt6.QtCore import (
    QObject, QRunnable, QStandardPaths, QThreadPool, Qt, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QImage, QPixmap


def default_image_cache_dir(sub: str = "images") -> Path:
    """Location for on-disk image bytes: reuse Qt's per-app cache dir so
    a user-triggered 'clear cache' picks it up automatically."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    if not base:
        base = str(Path.home() / ".cache" / "dopeiptv")
    return Path(base) / sub


def dir_size_bytes(path: Path) -> int:
    """Total on-disk size of *path*, best-effort (missing/unreadable files
    are silently skipped) so a broken symlink can't crash the Settings dialog."""
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def clear_directory(path: Path) -> None:
    """Delete every file under *path* but keep the directory itself so
    the LogoLoader can write into it again without needing a mkdir race."""
    if not path.exists():
        return
    for p in sorted(path.rglob("*"), key=lambda x: -len(x.parts)):
        try:
            if p.is_file() or p.is_symlink():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
        except OSError:
            pass


class WorkerSignals(QObject):
    """Signals emitted by Worker when the background task completes."""
    done = pyqtSignal(object)
    fail = pyqtSignal(str)
    finished = pyqtSignal()


class Worker(QRunnable):
    """QRunnable that executes a function in the thread pool."""

    def __init__(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        # QThreadPool must not delete us on the pool thread: WorkerSignals
        # lives in the main thread and would otherwise be destroyed from the
        # wrong thread mid-signal-delivery.
        self.setAutoDelete(False)
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        # Any exception that escapes this method surfaces via PyQt as
        # qFatal on newer Qt/PyQt versions and terminates the process
        # via SIGABRT. Trap absolutely everything - including
        # SystemExit / KeyboardInterrupt / anything raised while
        # signal-emit is dispatching - and forward the message
        # through .fail so run_async's on_fail path still runs.
        # A traceback here would spam stderr on every routine
        # network miss (DNS unreachable, dead provider host, 5xx)
        # since those all raise inside fetch; on_fail already gets
        # the message string, and any genuinely-uncaught crash is
        # caught by the installed sys.excepthook.
        try:
            result = self.fn(*self.args, **self.kwargs)
        except BaseException as e:
            try:
                self.signals.fail.emit(str(e))
            except BaseException:
                pass
        else:
            try:
                self.signals.done.emit(result)
            except BaseException:
                pass
        finally:
            try:
                self.signals.finished.emit()
            except BaseException:
                pass


_ACTIVE_WORKERS: set[Worker] = set()


def run_async(pool: QThreadPool, fn: Callable, on_done: Callable,
              on_fail: Callable | None = None, *args: Any,
              **kwargs: Any) -> Worker:
    """Schedule *fn* on *pool* and connect done/fail callbacks.

    Callbacks are wired with an explicit QueuedConnection so they run
    on the receiving QObject's thread regardless of PyQt's heuristics
    for plain-callable targets - which on some builds default to
    DirectConnection and dispatch on the worker thread, so a
    QPixmap/QWidget touched in on_done/on_fail then trips a
    thread-affinity qFatal and aborts the process."""
    w = Worker(fn, *args, **kwargs)
    ct = Qt.ConnectionType.QueuedConnection
    w.signals.done.connect(on_done, ct)
    if on_fail:
        w.signals.fail.connect(on_fail, ct)
    _ACTIVE_WORKERS.add(w)
    w.signals.finished.connect(
        lambda: _ACTIVE_WORKERS.discard(w), ct)
    pool.start(w)
    return w


class LogoLoader(QObject):
    """Asynchronous image downloader with in-memory cache.

    *max_size* bounds the cached pixmap (aspect-ratio preserved) - keep
    this small for tiny list icons and larger for anything meant to be
    shown big (posters, cast photos), since a low-res cache blurs badly
    once scaled back up.
    """

    def __init__(self, pool: QThreadPool, max_size: int = 96,
                 max_entries: int = 2000,
                 cache_dir: Path | str | None = None) -> None:
        super().__init__()
        self.pool = pool
        self.max_size = max_size
        self.max_entries = max_entries
        # Bounded LRU: older entries drop out as we scroll through big
        # provider dumps so the process doesn't grow unbounded.
        self.cache: OrderedDict[str, QPixmap] = OrderedDict()
        self.waiting: dict[str, list[Callable]] = {}
        # Failed URLs get a per-URL expiry timestamp. 404/410/451 get
        # a long TTL because the endpoint is genuinely gone; any other
        # failure (timeout, 500, connection reset) gets a much shorter
        # cooldown so the delegate doesn't re-queue the same URL on
        # every paint and drown the pool in retrying jobs while still
        # being able to retry once the hiccup is over.
        self.dead: dict[str, float] = {}
        self.dead_ttl_permanent: float = 3600.0
        self.dead_ttl_transient: float = 20.0
        # Host-level circuit breaker. A provider whose image server is
        # down (or resetting connections) has hundreds of URLs in one
        # category; without this, each dead URL burns a worker for up
        # to its full connect timeout and the queue starves out the
        # hosts that DO work (TMDB). Three straight failures on a host
        # put the whole host on a cooldown so every remaining URL on
        # it short-circuits to the fallback instantly; one success
        # resets the counter.
        self.dead_hosts: dict[str, float] = {}
        self._host_strikes: dict[str, int] = {}
        self.host_strike_limit: int = 3
        self.host_cooldown: float = 60.0
        # Disk cache: raw response bytes, keyed by URL hash. Lets
        # evicted RAM entries reload in a few ms from local SSD instead
        # of re-hitting the network.
        self.disk_dir: Path | None = (
            Path(cache_dir) if cache_dir is not None else None)

    @staticmethod
    def _host_of(url: str) -> str:
        # scheme://HOST[:port]/... - cheap split, no urlparse import.
        try:
            return url.split("/", 3)[2].lower()
        except IndexError:
            return url

    def _mark_dead(self, url: str, ttl: float) -> None:
        self.dead[url] = time.monotonic() + ttl

    def _strike_host(self, url: str) -> None:
        host = self._host_of(url)
        n = self._host_strikes.get(host, 0) + 1
        self._host_strikes[host] = n
        if (n >= self.host_strike_limit
                and host not in self.dead_hosts):
            _img_dbg(f"host DEAD({self.host_cooldown:.0f}s) {host}")
            self.dead_hosts[host] = time.monotonic() + self.host_cooldown

    def _clear_host(self, url: str) -> None:
        host = self._host_of(url)
        self._host_strikes.pop(host, None)
        self.dead_hosts.pop(host, None)

    def is_dead(self, url: str | None) -> bool:
        if not url:
            return False
        now = time.monotonic()
        host_expiry = self.dead_hosts.get(self._host_of(url))
        if host_expiry is not None:
            if now < host_expiry:
                return True
            self.dead_hosts.pop(self._host_of(url), None)
            self._host_strikes.pop(self._host_of(url), None)
        expiry = self.dead.get(url)
        if expiry is None:
            return False
        if now >= expiry:
            self.dead.pop(url, None)
            return False
        return True

    def _disk_path(self, url: str) -> Path | None:
        if self.disk_dir is None:
            return None
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.disk_dir / h[:2] / h[2:]

    def _store(self, url: str, pm: QPixmap) -> None:
        self.cache[url] = pm
        self.cache.move_to_end(url)
        # A URL that once failed but now decoded fine is alive again -
        # and so is its host.
        self.dead.pop(url, None)
        self._clear_host(url)
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)

    def get(self, url: str | None, callback: Callable[[QPixmap], None]) -> None:
        if not url:
            return
        if url in self.cache:
            self.cache.move_to_end(url)
            callback(self.cache[url])
            return
        if url in self.waiting:
            self.waiting[url].append(callback)
            return
        self.waiting[url] = [callback]
        disk_path = self._disk_path(url)

        def fetch(u: str = url, dp: Path | None = disk_path) -> tuple[str, bytes]:
            # Disk cache read: validate by attempting to decode. If a
            # previous session was killed mid-write (e.g. crash on
            # shutdown) the file is truncated and QPixmap.loadFromData
            # will fail - in that case unlink the bad file and fall
            # through to a fresh network fetch so the cover isn't
            # stuck as a placeholder forever.
            if dp is not None and dp.exists():
                try:
                    data = dp.read_bytes()
                    if data:
                        # QImage is thread-safe; QPixmap is GUI-thread
                        # only in Qt6 and touching it here aborts the
                        # whole process on newer PyQt6 (qFatal fires
                        # from pyqt6_err_print). We only need to
                        # validate that the bytes decode - the actual
                        # QPixmap conversion happens in done() which
                        # runs on the main thread.
                        probe = QImage()
                        if probe.loadFromData(data):
                            _img_dbg(f"disk hit {u}")
                            return u, data
                        _img_dbg(f"disk CORRUPT, refetching {u}")
                        dp.unlink(missing_ok=True)
                except OSError:
                    try:
                        dp.unlink(missing_ok=True)
                    except OSError:
                        pass
            # Short connect timeout: a host that's down should cost a
            # worker ~3 s, not the full read timeout - with hundreds
            # of URLs on one dead provider host that difference is
            # what keeps the queue from starving the working hosts.
            r = requests.get(u, headers=_IMG_HEADERS, timeout=(3.05, 15))
            # ANY HTTP response - even a 404 - proves the host itself
            # is alive, so reset its circuit-breaker strikes. Only
            # connection-level failures (reset, timeout, DNS) should
            # ever take a whole host down; a panel with some missing
            # image files must not have its working covers blocked.
            self._clear_host(u)
            # Long TTL for legitimately-dead endpoints (and 400s -
            # a malformed URL from provider data never starts
            # working), short TTL for everything else so a temporary
            # 500 gets a brief cooldown instead of a session-long ban.
            if r.status_code in (400, 404, 410, 451):
                _img_dbg(f"net {r.status_code} DEAD(1h) {u}")
                self._mark_dead(u, self.dead_ttl_permanent)
            r.raise_for_status()
            data = r.content
            _img_dbg(f"net {r.status_code} ok {len(data)}B {u}")
            if dp is not None:
                try:
                    dp.parent.mkdir(parents=True, exist_ok=True)
                    # Write to a sibling temp path and rename so a crash
                    # mid-write can never leave a truncated file behind
                    # for the next session to trip over.
                    tmp = dp.with_name(dp.name + ".part")
                    tmp.write_bytes(data)
                    tmp.replace(dp)
                except OSError as e:
                    _img_dbg(f"disk WRITE FAILED {e} {u}")
            return u, data

        def done(result: tuple[str, bytes]) -> None:
            u, data = result
            callbacks = self.waiting.pop(u, [])
            pm = QPixmap()
            if not pm.loadFromData(data):
                _img_dbg(f"DECODE FAILED {len(data)}B {u}")
                return
            pm = pm.scaled(self.max_size, self.max_size,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            self._store(u, pm)
            for cb in callbacks:
                try:
                    cb(pm)
                except RuntimeError:
                    pass

        def on_fail(_msg: str) -> None:
            # Cool the URL down briefly so the delegate doesn't re-fire
            # a fetch for it on every subsequent paint - that's what
            # let a single flaky endpoint saturate the pool with
            # repeat attempts and eventually starve every other
            # cover. If fetch() already marked it dead with the
            # permanent TTL (real 404), keep that longer timeout.
            if url not in self.dead:
                _img_dbg(f"net FAIL cooldown(20s) {_msg[:80]} {url}")
                self._mark_dead(url, self.dead_ttl_transient)
            # Only CONNECTION-level failures count toward the host
            # circuit breaker. An HTTP error response ("404 Client
            # Error: ...", "500 Server Error: ...") proves the host
            # answered - a panel with missing image files must not
            # get its working covers blocked. requests formats status
            # failures with the code first, so that prefix is the
            # discriminator.
            if not re.match(r"\s*\d{3} (Client|Server) Error", _msg):
                self._strike_host(url)
            callbacks = self.waiting.pop(url, [])
            for cb in callbacks:
                try:
                    cb(QPixmap())
                except RuntimeError:
                    pass

        run_async(self.pool, fetch, done, on_fail)
