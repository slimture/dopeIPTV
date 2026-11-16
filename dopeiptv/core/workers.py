"""Thread-pool workers and asynchronous logo loader."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .log import log

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
    # Verbose image-pipeline trace (RAM/disk/network hit, HTTP status, decode
    # failure, dead-marking), shown with DOPEIPTV_LOG=debug - the support tool
    # for "covers aren't loading" reports.
    log.debug("[img] %s", msg)


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


def is_tmdb_image_url(url: str | None) -> bool:
    """True when *url*'s HOST is image.tmdb.org. A plain substring check
    would also match the string at any position in a provider URL (e.g.
    http://panel.example/image.tmdb.org/x.jpg) and misclassify it - the
    CodeQL incomplete-url-substring-sanitization pattern."""
    try:
        return urlparse(url or "").hostname == "image.tmdb.org"
    except ValueError:
        return False


def tmdb_url_from_provider(url: str | None) -> str | None:
    """If *url* is a provider image URL that embeds a TMDB poster path,
    return the direct image.tmdb.org URL for it; otherwise None."""
    if not url or is_tmdb_image_url(url):
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


def choose_cover_url(title_tmdb, raw_provider, kind, is_dead):
    """Pick the cover URL for a list row, in priority order:

      1. the TMDB title-search poster (None when there's no TMDB key
         configured, or the lookup is still pending / found nothing)
      2. a TMDB poster path embedded in the provider's own image URL -
         rewritten to image.tmdb.org, which needs NO API key, so this
         works even for users who never connect a TMDB account
      3. the raw provider cover URL

    Returns the first candidate that isn't blacklisted (``is_dead(url)``
    covers both the single-URL cooldown and the per-host circuit
    breaker). When an embedded-TMDB URL is dead, the raw provider URL
    is skipped too - it's the panel's proxy of the same dead token.

    Pure function shared by MainWindow.cover_url and the tests so the
    two can't drift; the no-TMDB path is exercised directly by passing
    title_tmdb=None."""
    embed = (tmdb_url_from_provider(raw_provider)
             if kind in ("vod", "series") else None)
    if title_tmdb and not is_dead(title_tmdb):
        return title_tmdb
    if embed:
        return None if is_dead(embed) else embed
    if raw_provider and not is_dead(raw_provider):
        return raw_provider
    return None

from ._lazy_requests import requests
from PyQt6.QtCore import (
    QBuffer, QIODevice, QObject, QRunnable, QStandardPaths, QThreadPool, QTimer,
    Qt, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QImage, QImageReader, QPixmap

#: The formats a QPixmap keeps a picture in on the raster backends: an image
#: already in one becomes a pixmap without a conversion on the GUI thread.
_PIXMAP_FORMATS = (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32_Premultiplied)


def decode_image(data: bytes, fit: int = 0) -> QImage | None:
    """*data* as a picture, or None when it is none - on the calling thread,
    which is a worker's, never the GUI thread's.

    Through QImageReader.read, which lets go of the GIL while it decodes:
    PyQt holds it through QImage.loadFromData (and QPixmap's), so a worker
    decoding a large JPEG that way stopped the window's Python for as long.
    *fit*: then scaled to fit a fit x fit square, as the image cache keeps it
    - the aspect kept, smooth, a smaller picture made larger. The same steps
    the GUI thread took through QPixmap - into the pixmap's own format
    first (a JPEG is in it already), then that very ``scaled(fit, fit,
    KeepAspectRatio, SmoothTransformation)`` - so the pixels are the same;
    not the reader's own scaled decoding, whose pixels differ a little. The
    result becomes a QPixmap without a conversion."""
    buf = _read_only(data)
    if buf is None:
        return None
    img = QImageReader(buf).read()
    if img.isNull():
        return None
    if img.format() not in _PIXMAP_FORMATS:
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied
                                  if img.hasAlphaChannel() else QImage.Format.Format_RGB32)
    if fit > 0 and img.size().scaled(fit, fit, Qt.AspectRatioMode.KeepAspectRatio) != img.size():
        img = img.scaled(fit, fit, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return img


def is_picture(data: bytes) -> bool:
    """Whether *data* decodes as a picture: decoded whole (a truncated file
    fails), on a worker, the GIL let go meanwhile - as ``decode_image``."""
    buf = _read_only(data)
    return buf is not None and not QImageReader(buf).read().isNull()


def _read_only(data: bytes) -> QBuffer | None:
    """*data* as a device to read a picture from; None when there is none.
    The caller keeps it while a reader reads from it."""
    if not data:
        return None
    buf = QBuffer()
    buf.setData(data)
    return buf if buf.open(QIODevice.OpenModeFlag.ReadOnly) else None


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
                # The user's language where the error has one.
                localized = getattr(e, "localized", None)
                self.signals.fail.emit(
                    localized() if callable(localized) else str(e))
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


class _GuiCalls(QObject):
    """Calls handed to the GUI thread (on_gui_thread). Made on whichever
    thread asks first and moved to the GUI thread at once, so the queued
    signal is always delivered there."""

    call = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(object)
    def _run(self, job: object) -> None:
        fn, args = job  # type: ignore[misc]
        try:
            fn(*args)
        except Exception as e:
            log.info("a call for the window's thread failed: %s", e)


_gui_calls: _GuiCalls | None = None
_gui_lock = threading.Lock()


def on_gui_thread(fn: Callable[..., Any], *args: Any) -> None:
    """Run fn(*args) on the GUI thread: at once when called there (or
    before there is an application), else queued to it - for what must
    never happen on a worker, a QSettings write above all (one QSettings
    object is not safe to share between threads)."""
    from PyQt6.QtCore import QCoreApplication, QThread
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() is app.thread():
        fn(*args)
        return
    global _gui_calls
    from PyQt6 import sip
    with _gui_lock:
        calls = _gui_calls
        # Made again when the application it was made for has gone (a
        # test's, before another's): Qt deleted it with that one.
        if calls is None or sip.isdeleted(calls) or calls.thread() is not app.thread():
            calls = _GuiCalls()
            calls.moveToThread(app.thread())
            _gui_calls = calls
    calls.call.emit((fn, args))


def _image_get(url: str) -> Callable[..., Any]:
    """requests.get for an image - on the playing source's route while
    Nerd gives it one (core/net_route.py: own DNS, IP version, a pinned
    certificate)."""
    from . import net_route
    route = net_route.current()
    if not route.active:
        return requests.get
    from .net_adapter import getter
    return getter(route)


def _ignore_result(_result: object) -> None:
    """on_done for work that is done for its side effect."""


class LogoLoader(QObject):
    """Asynchronous image downloader with in-memory cache.

    *max_size* bounds the cached pixmap (aspect-ratio preserved) - keep
    this small for tiny list icons and larger for anything meant to be
    shown big (posters, cast photos), since a low-res cache blurs badly
    once scaled back up.

    A picture is decoded once, on the loader's pool, already at that size
    (``decode_image``: the GIL let go while it decodes); the GUI thread only
    makes the pixmap of it, never a decode or a scale.
    """

    def __init__(self, pool: QThreadPool, max_size: int = 96,
                 max_entries: int = 2000,
                 cache_dir: Path | str | None = None,
                 max_bytes: int = 64 * 1024 * 1024,
                 disk_budget: int = 2560 * 1024 * 1024) -> None:
        super().__init__()
        self.pool = pool
        self.max_size = max_size
        self.max_entries = max_entries
        # Bounded LRU: older entries drop out as we scroll through big
        # provider dumps so the process doesn't grow unbounded. The bound
        # is in BYTES, not entries - 2000 pixmaps sounds harmless but at
        # 320 px it is close to a gigabyte of decoded RGBA sitting in RAM
        # after a long browse. Evicted entries reload from the disk cache
        # in a few ms, so a tight budget costs nothing visible.
        self.max_bytes = max_bytes
        self._cache_bytes = 0
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
        self.host_strike_limit: int = 2
        self.host_cooldown: float = 60.0
        # Disk cache: raw response bytes, keyed by URL hash. Lets
        # evicted RAM entries reload in a few ms from local SSD instead
        # of re-hitting the network.
        self.disk_dir: Path | None = (
            Path(cache_dir) if cache_dir is not None else None)
        # The disk cache had NO automatic bound - it grew for every image
        # ever fetched until the user found the manual clear button in
        # Settings. Prune it back under budget (2.5 GB - generous, covers
        # several full provider dumps; disk is cheap, re-downloads are not)
        # once per session, off the GUI thread. 0 disables (tests drive
        # _prune_disk_cache directly).
        self.disk_budget = int(disk_budget)
        if self.disk_dir is not None and self.disk_budget > 0:
            # Deferred a few seconds so the prune's directory walk never
            # competes with the startup burst (EPG cache load, first list
            # fetch) and short-lived sessions skip it entirely. Parented
            # timer: dies with the loader, so it can never fire on a
            # destroyed object. A bound method, not a lambda over self: the
            # lambda closed a reference cycle through the timer, and the
            # garbage collector could clear its closure before the timer
            # fired - a segfault in the timeout.
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(5000)
            t.timeout.connect(self._start_prune)
            t.start()

    def _start_prune(self) -> None:
        run_async(self.pool, self._prune_disk_cache, _ignore_result)

    def _prune_disk_cache(self) -> int:
        """Delete the oldest cached image files until the disk cache fits
        the budget again. Runs on a pool thread (pure filesystem work, no
        Qt objects). Returns the number of bytes freed, for the tests."""
        d = self.disk_dir
        if d is None or not d.exists():
            return 0
        files: list[tuple[float, int, Path]] = []
        total = 0
        for p in d.rglob("*"):
            try:
                if not p.is_file():
                    continue
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= self.disk_budget:
            return 0
        freed = 0
        # Oldest first, and stop at 90% of the budget so the next session
        # doesn't immediately have to prune again.
        target = int(self.disk_budget * 0.9)
        for _mtime, size, p in sorted(files):
            try:
                p.unlink()
            except OSError:
                continue
            total -= size
            freed += size
            if total <= target:
                break
        return freed

    @staticmethod
    def _host_of(url: str) -> str:
        # scheme://HOST[:port]/... - cheap split, no urlparse import.
        try:
            return url.split("/", 3)[2].lower()
        except IndexError:
            return url

    #: Hard cap on the dead-URL tombstone map. Entries are only ever removed
    #: when the SAME url is re-requested after expiry - never for urls you
    #: scrolled past once - so a long browse over providers with many broken
    #: image links grew it without bound.
    DEAD_CAP = 4096

    def _mark_dead(self, url: str, ttl: float) -> None:
        # Trimmed in place, never rebound: the window hands one dict to both
        # image loaders, and a new dict here quietly ended that sharing.
        self.dead[url] = time.monotonic() + ttl
        if len(self.dead) > self.DEAD_CAP:
            now = time.monotonic()
            entries = list(self.dead.items())
            for u, t in entries:
                if t <= now:
                    self.dead.pop(u, None)
            if len(self.dead) > self.DEAD_CAP:
                # Still over after dropping expired ones: keep the entries
                # with the longest remaining cooldown (permanent 404s), the
                # short transient cooldowns are the cheapest to re-learn.
                ranked = sorted(self.dead.items(), key=lambda kv: kv[1])
                for u, _t in ranked[:len(ranked) - self.DEAD_CAP]:
                    self.dead.pop(u, None)

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

    def disk_file(self, url: str) -> Path | None:
        """The cached copy of *url* on disk, when there is one."""
        path = self._disk_path(url)
        return path if path is not None and path.is_file() else None

    #: Answers fetch_file takes beside image/*: the generic types some image
    #: hosts (S3 buckets, panels) send - kept only when the bytes are a picture.
    _GENERIC_TYPES = ("application/octet-stream", "binary/octet-stream")

    def fetch_file(self, url: str, timeout: float = 5.0,
                   max_bytes: int = 2 * 1024 * 1024) -> Path | None:
        """*url* fetched into the disk cache now, on the calling thread -
        never the window's (it blocks for up to *timeout*): the file
        ``disk_file`` reads, or None - no disk cache, not a web address, the
        address or its host on cooldown, an answer that is not an image or
        is larger than *max_bytes*. As the lists' own fetches: the same user
        agent and route, the same dead-address and host cooldowns. No Qt
        object but a QImageReader (thread-safe), to check the bytes decode."""
        dp = self._disk_path(url)
        if dp is None or not url.lower().startswith(("http://", "https://")):
            return None
        if dp.is_file():
            return dp
        if self.is_dead(url):
            return None
        try:
            r = _image_get(url)(url, headers=_IMG_HEADERS, stream=True,
                                timeout=(min(3.05, timeout), timeout))
        except Exception as e:
            _img_dbg(f"file FAIL {type(e).__name__} {url}")
            self._mark_dead(url, self.dead_ttl_transient)
            self._strike_host(url)
            return None
        try:
            self._clear_host(url)
            if r.status_code != 200:
                permanent = r.status_code in (400, 404, 410, 451)
                self._mark_dead(url, self.dead_ttl_permanent if permanent
                                else self.dead_ttl_transient)
                _img_dbg(f"file {r.status_code} {url}")
                return None
            ctype = str(r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not (ctype.startswith("image/") or ctype in self._GENERIC_TYPES):
                _img_dbg(f"file not an image ({ctype or 'no type'}) {url}")
                self._mark_dead(url, self.dead_ttl_permanent)
                return None
            try:
                said = int(r.headers.get("Content-Length") or 0)
            except ValueError:
                said = 0
            data = b""
            if said <= max_bytes:
                parts, size = [], 0
                for chunk in r.iter_content(64 * 1024):
                    parts.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        break
                data = b"".join(parts)
            if said > max_bytes or len(data) > max_bytes:
                # Too big for this reader only: the lists may still show it.
                _img_dbg(f"file too big {url}")
                return None
            if not is_picture(data):
                _img_dbg(f"file not a picture ({len(data)}B) {url}")
                self._mark_dead(url, self.dead_ttl_permanent)
                return None
            dp.parent.mkdir(parents=True, exist_ok=True)
            tmp = dp.with_name(dp.name + f".{threading.get_ident()}.part")
            tmp.write_bytes(data)
            tmp.replace(dp)
            _img_dbg(f"file ok {len(data)}B {url}")
            return dp
        except Exception as e:
            _img_dbg(f"file FAIL {type(e).__name__} {url}")
            self._mark_dead(url, self.dead_ttl_transient)
            return None
        finally:
            r.close()

    @staticmethod
    def _pm_bytes(pm: QPixmap) -> int:
        # Decoded pixmap cost: width x height x 4 (RGBA). Never zero, so
        # even null pixmaps count as an entry.
        return max(1024, pm.width() * pm.height() * 4)

    def _store(self, url: str, pm: QPixmap) -> None:
        old = self.cache.pop(url, None)
        if old is not None:
            self._cache_bytes -= self._pm_bytes(old)
        self.cache[url] = pm
        self._cache_bytes += self._pm_bytes(pm)
        # A URL that once failed but now decoded fine is alive again -
        # and so is its host.
        self.dead.pop(url, None)
        self._clear_host(url)
        while self.cache and (len(self.cache) > self.max_entries
                              or self._cache_bytes > self.max_bytes):
            _, evicted = self.cache.popitem(last=False)
            self._cache_bytes -= self._pm_bytes(evicted)

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
        # Bound to a local before it becomes a default argument: the empty
        # case returned above, so url is a str from here on - but a default
        # expression is typed where it is written, not where the narrowing
        # happened, so the parameter took the declared `str | None`.
        src: str = url
        fit = self.max_size

        # fetch() answers with the picture decoded here, on the pool, at the
        # size the cache keeps (decode_image): None when nothing was served
        # (skipped, gone, unreadable), a null QImage when what was served is
        # no picture. A QImage, never a QPixmap: QPixmap is GUI-thread only in
        # Qt6 and touching one here aborts the whole process on newer PyQt6.
        def fetch(u: str = src, dp: Path | None = disk_path) -> tuple[str, QImage | None]:
            # A local absolute path (a generated video thumbnail, never a
            # provider URL) is read straight off disk - no network machinery,
            # no circuit breaker, no second disk cache.
            if u.startswith("/") or u.startswith("file://"):
                try:
                    data = Path(u.removeprefix("file://")).read_bytes()
                except OSError:
                    return u, None
                return u, decode_image(data, fit)
            # Disk cache read: validate by decoding it. If a previous
            # session was killed mid-write (e.g. crash on shutdown) the file
            # is truncated and does not decode - in that case unlink the bad
            # file and fall through to a fresh network fetch so the cover
            # isn't stuck as a placeholder forever.
            if dp is not None and dp.exists():
                try:
                    data = dp.read_bytes()
                    if data:
                        img = decode_image(data, fit)
                        if img is not None:
                            _img_dbg(f"disk hit {u}")
                            return u, img
                        _img_dbg(f"disk CORRUPT, refetching {u}")
                        dp.unlink(missing_ok=True)
                except OSError:
                    try:
                        dp.unlink(missing_ok=True)
                    except OSError:
                        pass
            # A host that tripped the circuit breaker WHILE this job sat
            # in the queue: skip the network entirely. Scrolling a big
            # category queues hundreds of jobs for one host before the
            # first couple fail and mark it dead; without this re-check,
            # every already-queued job still spends its full connect
            # timeout on a host we already know is down. Returning nothing
            # makes done() paint nothing (placeholder) without a network
            # round-trip or another strike.
            if self.is_dead(u):
                _img_dbg(f"host cooldown skip {u}")
                return u, None
            # Short connect timeout: a host that's down should cost a
            # worker ~3 s, not the full read timeout - with hundreds
            # of URLs on one dead provider host that difference is
            # what keeps the queue from starving the working hosts.
            r = _image_get(u)(u, headers=_IMG_HEADERS, timeout=(3.05, 15))
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
            if not data:
                return u, None
            img = decode_image(data, fit)
            # A 200 that is not an image (a panel's HTML error page) must not
            # reach the disk cache: the next paint read it back, found it
            # corrupt, deleted it and fetched again - on every paint.
            if dp is not None and img is not None:
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
            return u, img if img is not None else QImage()

        def done(result: tuple[str, QImage | None]) -> None:
            u, img = result
            callbacks = self.waiting.pop(u, [])
            if img is None:
                # Nothing = skipped (host on cooldown) or nothing served.
                # Just drop the pending entry; the delegate keeps the
                # placeholder and will re-try once the cooldown expires.
                # Nothing served and no cooldown yet (a local thumbnail that
                # is gone or unreadable) gets a short one, or every paint
                # queued the same failing read again.
                if not self.is_dead(u):
                    self._mark_dead(u, self.dead_ttl_transient)
                return
            # Decoded and scaled on the pool already: only the pixmap is
            # made here, from an image in a format it takes as it is.
            pm = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
            if pm.isNull():
                _img_dbg(f"DECODE FAILED DEAD(1h) {u}")
                self._mark_dead(u, self.dead_ttl_permanent)
                for cb in callbacks:
                    try:
                        cb(QPixmap())
                    except RuntimeError:
                        pass
                return
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
