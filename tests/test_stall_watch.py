"""The debug log says where the window's thread is when it stops answering
(app._watch_stalls): what GNOME's spinning pointer means, found without a
debugger - and nothing at all unless DOPEIPTV_LOG=debug."""
from __future__ import annotations

import logging
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv import app as app_mod  # noqa: E402
from dopeiptv.core.log import log  # noqa: E402


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


class _Keep(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _pump(qapp, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_off_unless_the_log_is_at_debug(qapp):
    before = log.level
    log.setLevel(logging.INFO)
    try:
        assert app_mod._watch_stalls(qapp, stall_s=0.2, every=0.05) is False
    finally:
        log.setLevel(before)


def test_a_stall_is_said_once_with_where_the_thread_was(qapp):
    keep = _Keep()
    before = log.level
    log.addHandler(keep)
    log.setLevel(logging.DEBUG)
    try:
        assert app_mod._watch_stalls(qapp, stall_s=0.3, every=0.05)
        _pump(qapp, 0.3)
        assert not [m for m in keep.lines if m.startswith("stall:")]   # answering: nothing

        def stuck_here():
            time.sleep(0.9)             # the window's thread held, as a block would

        stuck_here()
        _pump(qapp, 0.4)
        stalls = [m for m in keep.lines if m.startswith("stall:")]
        assert len(stalls) == 1, stalls           # once per stall, not every look
        assert "stuck_here" in stalls[0] and "time.sleep(0.9)" in stalls[0]
    finally:
        log.setLevel(before)
        log.removeHandler(keep)
