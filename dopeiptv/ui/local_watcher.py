"""Local files' folders watched for changes, on a thread of their own (#34).

``DirWatcher`` holds a ``QFileSystemWatcher`` that lives on its own
``QThread``: adding a folder to a watch asks the file system about it (a
stat, an inotify watch, a kqueue descriptor), and none of that may happen
on the window's thread - a folder on a slow disk would hold the window,
and the player with it. ``watch(paths)`` hands the folders over (the ones
watched before and not in it are let go; one that is not there any more is
left out, quietly); ``changed(path)`` comes back on the window's thread,
once per change the system reports. What the changes mean - when to read
again, what changed - is core/local_watch.py's.

``stop()`` lets every folder go and ends the thread (the window's close,
the application's end): a thread still running when Qt goes would take
the process down.
"""

from __future__ import annotations

import atexit
import os
import weakref

from PyQt6 import sip
from PyQt6.QtCore import (
    QCoreApplication, QFileSystemWatcher, QObject, QThread, pyqtSignal, pyqtSlot,
)

from ..core.log import log

__all__ = ["DirWatcher"]

#: Every watcher not stopped yet, held here: one whose window went without
#: its close is never let go with its thread still running (Qt would take
#: the process down) - the application's end stops it.
_LIVE: set = set()


class _Worker(QObject):
    """The watcher's side, on its thread: made there, so its watcher and
    the system's notifications are that thread's too."""

    changed = pyqtSignal(str)
    applied = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self._fs: QFileSystemWatcher | None = None

    @pyqtSlot(object)
    def apply(self, paths) -> None:
        try:
            want = [p for p in paths or () if isinstance(p, str) and p]
            fs = self._fs
            if fs is None:
                if not want:
                    self.applied.emit(0)
                    return
                fs = self._fs = QFileSystemWatcher(self)
                fs.directoryChanged.connect(self.changed)
                fs.fileChanged.connect(self.changed)
            have = set(fs.directories())
            keep = set(want)
            gone = [p for p in have if p not in keep]
            if gone:
                fs.removePaths(gone)
            # A folder gone since it was listed is left out, without the
            # warning Qt would print for it.
            new = [p for p in want if p not in have and os.path.isdir(p)]
            if new:
                failed = fs.addPaths(new)
                if failed:
                    log.debug("local watch: %d folders not watched", len(failed))
            self.applied.emit(len(fs.directories()))
        except Exception as e:      # noqa: BLE001 - a watch is a nicety
            log.debug("local watch: %s", e)

    @pyqtSlot()
    def clear(self) -> None:
        fs = self._fs
        if fs is not None:
            dirs, files = fs.directories(), fs.files()
            if dirs or files:
                fs.removePaths(dirs + files)
        self.applied.emit(0)

    @pyqtSlot()
    def close(self) -> None:
        """The watcher gone on its own thread, then the thread ended."""
        fs, self._fs = self._fs, None
        try:
            if fs is not None:
                self._fs = fs
                self.clear()
                self._fs = None
                sip.delete(fs)
        except Exception as e:      # noqa: BLE001 - it is going anyway
            log.debug("local watch: closing: %s", e)
        QThread.currentThread().quit()


class DirWatcher(QObject):
    """Folders watched on a thread of its own; ``changed`` on the window's."""

    changed = pyqtSignal(str)
    _want = pyqtSignal(object)
    _clear = pyqtSignal()
    _close = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.watching: tuple[str, ...] = ()
        #: How many the system took, as the thread last said.
        self.count = 0
        self._stopped = False
        self._thread = QThread()
        self._thread.setObjectName("dopeiptv-local-watch")
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)
        self._want.connect(self._worker.apply)
        self._clear.connect(self._worker.clear)
        self._close.connect(self._worker.close)
        self._worker.changed.connect(self._on_changed)
        self._worker.applied.connect(self._on_applied)
        self._thread.start()
        _LIVE.add(self)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)
        ref = weakref.ref(self)
        atexit.register(lambda: (lambda w: w.stop() if w is not None else None)(ref()))

    def watch(self, paths) -> None:
        """Watch these folders (and no others)."""
        if self._stopped:
            return
        self.watching = tuple(paths or ())
        self._want.emit(list(self.watching))

    def clear(self) -> None:
        if self._stopped:
            return
        self.watching = ()
        self._clear.emit()

    def running(self) -> bool:
        return not self._stopped and self._thread.isRunning()

    def stop(self) -> None:
        """Every folder let go and the thread ended (once)."""
        if self._stopped:
            return
        self._stopped = True
        self.watching = ()
        _LIVE.discard(self)
        try:
            if self._thread.isRunning():
                self._close.emit()
                if not self._thread.wait(3000):
                    self._thread.quit()
                    if not self._thread.wait(1000):
                        log.warning("local watch: its thread did not end")
        except RuntimeError:
            pass                        # already gone with Qt

    @pyqtSlot(str)
    def _on_changed(self, path: str) -> None:
        if not self._stopped:
            self.changed.emit(path)

    @pyqtSlot(int)
    def _on_applied(self, n: int) -> None:
        self.count = n
