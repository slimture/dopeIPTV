"""Thread-pool workers and asynchronous logo loader."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import requests
from PyQt6.QtCore import (
    QObject, QRunnable, QStandardPaths, QThreadPool, Qt, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QPixmap


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
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            self.signals.fail.emit(str(e))
        else:
            self.signals.done.emit(result)
        finally:
            self.signals.finished.emit()


_ACTIVE_WORKERS: set[Worker] = set()


def run_async(pool: QThreadPool, fn: Callable, on_done: Callable,
              on_fail: Callable | None = None, *args: Any,
              **kwargs: Any) -> Worker:
    """Schedule *fn* on *pool* and connect done/fail callbacks."""
    w = Worker(fn, *args, **kwargs)
    w.signals.done.connect(on_done)
    if on_fail:
        w.signals.fail.connect(on_fail)
    _ACTIVE_WORKERS.add(w)
    w.signals.finished.connect(lambda: _ACTIVE_WORKERS.discard(w))
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
        # Disk cache: raw response bytes, keyed by URL hash. Lets
        # evicted RAM entries reload in a few ms from local SSD instead
        # of re-hitting the network.
        self.disk_dir: Path | None = (
            Path(cache_dir) if cache_dir is not None else None)

    def _disk_path(self, url: str) -> Path | None:
        if self.disk_dir is None:
            return None
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.disk_dir / h[:2] / h[2:]

    def _store(self, url: str, pm: QPixmap) -> None:
        self.cache[url] = pm
        self.cache.move_to_end(url)
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
            if dp is not None and dp.exists():
                try:
                    data = dp.read_bytes()
                    if data:
                        return u, data
                except OSError:
                    pass
            r = requests.get(u, timeout=10)
            r.raise_for_status()
            data = r.content
            if dp is not None:
                try:
                    dp.parent.mkdir(parents=True, exist_ok=True)
                    dp.write_bytes(data)
                except OSError:
                    pass
            return u, data

        def done(result: tuple[str, bytes]) -> None:
            u, data = result
            callbacks = self.waiting.pop(u, [])
            pm = QPixmap()
            if pm.loadFromData(data):
                pm = pm.scaled(self.max_size, self.max_size,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self._store(u, pm)
                for cb in callbacks:
                    try:
                        cb(pm)
                    except RuntimeError:
                        pass

        run_async(self.pool, fetch, done, lambda _: self.waiting.pop(url, None))
