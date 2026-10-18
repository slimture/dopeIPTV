"""Keep Python's cyclic garbage collector on the GUI thread.

The collector runs on whichever thread crosses its allocation threshold -
here usually a pool thread fetching a logo, a list or the guide. Freeing a
cycle that holds Qt objects there tears them down off the GUI thread: the
C++ destructors run with the GIL released, and a lambda connected to a
signal can already have lost its closure while the signal can still fire.
The next timeout then calls a half-freed function and the process
segfaults (the test suite did, under load, in a QTimer's lambda).

So automatic collection is switched off, and the GUI thread collects on a
timer with the collector's own thresholds. Objects without cycles are
still freed at once by reference counting; only cycles wait for the tick.
A collection holds the GIL wherever it runs, so it paused the GUI thread
(and the player's paintGL) before as well; a short interval keeps each
young collection down to half a second's worth of new objects.
"""
from __future__ import annotations

import gc
import time

from PyQt6.QtCore import QObject, QTimer

from .log import log

INTERVAL_MS = 500
# A full collection walks every object in the process. The automatic
# collector rations those too (only once the old generation has grown by a
# quarter); a fixed spacing keeps a big guide from costing a pause a minute.
FULL_EVERY_S = 300.0


def collect_due(last_full: float, now: float,
                full_every: float = FULL_EVERY_S) -> tuple[int, float]:
    """Collect the generations whose thresholds have been crossed, as the
    automatic collector would. Returns the generation collected (-1 for
    none) and the time of the last full collection."""
    t0, t1, t2 = gc.get_threshold()
    c0, c1, c2 = gc.get_count()
    if t0 <= 0 or c0 <= t0:
        return -1, last_full
    gen = 0
    if c1 > t1:
        gen = 1
        if c2 > t2 and now - last_full >= full_every:
            gen = 2
    gc.collect(gen)
    return gen, (now if gen == 2 else last_full)


class GcGuard(QObject):
    """Owns the collection timer; create it once, on the GUI thread, right
    after the QApplication."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._last_full = time.monotonic()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        log.debug("gc: automatic collection off, collecting on the GUI "
                  "thread every %d ms", INTERVAL_MS)

    def _tick(self) -> None:
        _gen, self._last_full = collect_due(self._last_full,
                                            time.monotonic())
