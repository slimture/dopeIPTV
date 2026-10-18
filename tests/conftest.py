"""Suite-wide: cyclic garbage collection on the main thread only, between
tests - what the app does with dopeiptv/core/gcguard.py. Left automatic, a
collection triggered on a pool or cast thread tore Qt objects down off the
main thread, and the suite segfaulted under load in a QTimer's lambda."""
import gc
import time

import pytest

from dopeiptv.core.gcguard import collect_due

_state = {"last_full": time.monotonic()}


@pytest.fixture(autouse=True, scope="session")
def _no_automatic_gc():
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


@pytest.fixture(autouse=True)
def _collect_between_tests():
    yield
    _gen, _state["last_full"] = collect_due(
        _state["last_full"], time.monotonic(), full_every=10.0)
