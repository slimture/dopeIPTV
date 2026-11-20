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
# A real-window test runs its window in a child process that prints what it
# saw and ends with os._exit(0), which skips flushing stdout. Into a pipe that
# output is buffered, so it was lost - "exit 0, stdout=''" on the CI runner,
# while a shell that sets PYTHONUNBUFFERED passed. Children inherit this.
os.environ["PYTHONUNBUFFERED"] = "1"
# And a child that dies in Qt or mpv says where, instead of exiting with
# nothing but a Qt warning on stderr (as on a Windows run).
os.environ["PYTHONFAULTHANDLER"] = "1"


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
    every "/p/" in the next test's addresses."""
    from dopeiptv.core import log as app_log
    kept = set(app_log._short)
    yield
    app_log._short.clear()
    app_log._short.update(kept)


@pytest.fixture(autouse=True)
def _no_cast_source_left():
    """A cast's source (core/nerd.set_cast_source) is set as a cast starts:
    one a test started must not stand for the next test's Kodi or AirPlay."""
    yield
    from dopeiptv.core import nerd
    nerd.set_cast_source(None)
