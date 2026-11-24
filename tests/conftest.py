"""Suite-wide: cyclic garbage collection on the main thread only, between
tests - what the app does with dopeiptv/core/gcguard.py. Left automatic, a
collection triggered on a pool or cast thread tore Qt objects down off the
main thread, and the suite segfaulted under load in a QTimer's lambda."""
import collections
import gc
import os
import tempfile
import time

import pytest

from dopeiptv.core.gcguard import collect_due
from tests import shard

_state = {"last_full": time.monotonic()}

# The app's caches (channel lists, guides) for this run only: a test process
# has no application name, so they went straight into ~/.cache (lists_a.d,
# lists_main.d), and a list cached by an earlier run could stand in for a
# server that did not answer. Child processes inherit it.
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="dopeiptv-test-cache-")
# Log files and the crash file (core/report.py) too: never the real ones.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="dopeiptv-test-state-")
# A real-window test runs its window in a child process that prints what it
# saw and ends with os._exit(0), which skips flushing stdout. Into a pipe that
# output is buffered, so it was lost - "exit 0, stdout=''" on the CI runner,
# while a shell that sets PYTHONUNBUFFERED passed. Children inherit this.
os.environ["PYTHONUNBUFFERED"] = "1"
# And a child that dies in Qt or mpv says where, instead of exiting with
# nothing but a Qt warning on stderr (as on a Windows run).
os.environ["PYTHONFAULTHANDLER"] = "1"
# Offline mode by itself follows the system's word on the network
# (core/offline.py): never this machine's in a test - a laptop on a train
# would see every window offline. A test that wants it says a word of its
# own (DOPEIPTV_NETWORK=disconnected) in its child's environment.
os.environ["DOPEIPTV_NETWORK"] = "0"


def pytest_collection_modifyitems(config, items):
    """CI runs the suite in parts at once (tests/shard.py): with
    DOPEIPTV_SHARD=k/N only part k's test files run here."""
    try:
        spec = shard.parse(os.environ.get("DOPEIPTV_SHARD", ""))
    except ValueError as e:
        raise pytest.UsageError(str(e)) from None
    if spec is None:
        return
    k, n = spec
    counts = collections.Counter(shard.file_of(item.nodeid) for item in items)
    owner = shard.assign(dict(counts), shard.load_times(), n)
    keep, drop = [], []
    for item in items:
        (keep if owner[shard.file_of(item.nodeid)] == k - 1 else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep


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


@pytest.fixture(autouse=True)
def _online_again():
    """Offline mode is a process's state (core/offline.py): one a test
    turned on never stays on for the next."""
    yield
    from dopeiptv.core import offline
    offline.reset()


@pytest.fixture(autouse=True)
def _build_from_source():
    """Each test runs as a copy from source (core/build_check.py): the
    verdict is kept for the session, and a test that pretends to be a
    frozen bundle left "modified" behind for every test after it."""
    from dopeiptv.core import build_check
    build_check.set_verdict(build_check.Verdict(build_check.SOURCE))
    yield
    build_check.set_verdict(None)


@pytest.fixture(autouse=True)
def _no_short_login_left():
    """A login of three characters or fewer is masked as a word of its own
    (core/log.py): a test's client made with "u" and "p" must not mask
    every "/p/" in the next test's addresses. A longer one neither: a
    test's "root" masked the next test's /tmp/pytest-of-root/ path in what
    the notices said (they take every word through the log's mask)."""
    from dopeiptv.core import log as app_log
    kept, kept_long = set(app_log._short), set(app_log._secrets)
    yield
    app_log._short.clear()
    app_log._short.update(kept)
    app_log._secrets.clear()
    app_log._secrets.update(kept_long)


@pytest.fixture(autouse=True)
def _no_cast_source_left():
    """A cast's source (core/nerd.set_cast_source) is set as a cast starts:
    one a test started must not stand for the next test's Kodi or AirPlay."""
    yield
    from dopeiptv.core import nerd
    nerd.set_cast_source(None)
