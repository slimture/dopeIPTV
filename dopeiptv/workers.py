"""Thread-pool workers and asynchronous logo loader."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable

import requests
from PyQt6.QtCore import (
    QObject, QRunnable, QThreadPool, Qt, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QPixmap


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
                 max_entries: int = 800) -> None:
        super().__init__()
        self.pool = pool
        self.max_size = max_size
        self.max_entries = max_entries
        # Bounded LRU: older entries drop out as we scroll through big
        # provider dumps so the process doesn't grow unbounded.
        self.cache: OrderedDict[str, QPixmap] = OrderedDict()
        self.waiting: dict[str, list[Callable]] = {}

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

        def fetch(u: str = url) -> tuple[str, bytes]:
            r = requests.get(u, timeout=10)
            r.raise_for_status()
            return u, r.content

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
