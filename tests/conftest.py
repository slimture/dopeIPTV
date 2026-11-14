"""Suite-wide: cyclic garbage collection on the main thread only, between
tests - what the app does with dopeiptv/core/gcguard.py. Left automatic, a
collection triggered on a pool or cast thread tore Qt objects down off the
main thread, and the suite segfaulted under load in a QTimer's lambda."""
import gc
import os
import tempfile
import time

import pytest

from dopeiptv.core.gcguard import collect_due

_state = {"last_full": time.monotonic()}

# The app's caches (channel lists, guides) for this run only: a test process
# has no application name, so they went straight into ~/.cache (lists_a.d,
# lists_main.d), and a list cached by an earlier run could stand in for a
# server that did not answer. Child processes inherit it.
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="dopeiptv-test-cache-")
# Log files and the crash file (core/report.py) too: never the real ones.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="dopeiptv-test-state-")


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
