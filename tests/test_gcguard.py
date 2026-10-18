"""The cyclic garbage collector runs on the GUI thread only.

Collected on a pool thread, a reference cycle holding Qt objects was torn
down off the GUI thread; a lambda connected to a QTimer had its closure
cleared while the timer could still fire, and the next timeout segfaulted
(the full suite did, under load, in LogoLoader's prune timer).
"""
import gc
import os
import time
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import gcguard  # noqa: E402
from dopeiptv.core.workers import LogoLoader  # noqa: E402


class _Cycle:
    def __init__(self):
        self.me = self


def _garbage_cycles(n):
    for _ in range(n):
        _Cycle()


def test_collect_due_frees_cycles_once_the_threshold_is_crossed():
    gc.disable()
    gc.collect()
    probe = _Cycle()
    alive = weakref.ref(probe)
    del probe
    assert alive() is not None          # a cycle: refcounting cannot free it
    gen, _ = gcguard.collect_due(0.0, 0.0)
    assert gen == -1                    # below the threshold: nothing yet
    _garbage_cycles(gc.get_threshold()[0] + 10)
    gen, _ = gcguard.collect_due(0.0, 0.0)
    assert gen >= 0
    assert alive() is None


def test_a_full_collection_waits_for_its_spacing(monkeypatch):
    collected = []
    counts = {"now": (701, 11, 11)}
    monkeypatch.setattr(gcguard.gc, "get_threshold", lambda: (700, 10, 10))
    monkeypatch.setattr(gcguard.gc, "get_count", lambda: counts["now"])
    monkeypatch.setattr(gcguard.gc, "collect",
                        lambda generation=2: collected.append(generation))
    # Every count is over its threshold, but the last full one was 1 s ago.
    assert gcguard.collect_due(100.0, 101.0, full_every=300.0) == (1, 100.0)
    assert gcguard.collect_due(100.0, 500.0, full_every=300.0) == (2, 500.0)
    counts["now"] = (701, 3, 11)        # generation 1 not due: young only
    assert gcguard.collect_due(100.0, 900.0, full_every=300.0) == (0, 100.0)
    counts["now"] = (12, 11, 11)        # nothing allocated since: nothing
    assert gcguard.collect_due(100.0, 900.0, full_every=300.0) == (-1, 100.0)
    assert collected == [1, 2, 0]


def test_the_guard_turns_automatic_collection_off():
    QApplication.instance() or QApplication([])
    gc.enable()
    try:
        guard = gcguard.GcGuard()
        assert not gc.isenabled()
        guard._tick()                    # collects without raising
        guard.deleteLater()
    finally:
        gc.disable()                     # the suite's own policy (conftest)


def test_a_logo_loader_with_a_disk_cache_leaves_no_cycle(tmp_path):
    """Its prune timer was connected to a lambda over the loader: a cycle
    through the timer, so only the collector could free it - and the
    collector could clear the lambda before the timer fired."""
    QApplication.instance() or QApplication([])
    gc.disable()
    gc.collect()
    loader = LogoLoader(QThreadPool(), cache_dir=tmp_path)
    alive = weakref.ref(loader)
    del loader
    deadline = time.monotonic() + 1.0
    while alive() is not None and time.monotonic() < deadline:
        QApplication.processEvents()
    assert alive() is None
