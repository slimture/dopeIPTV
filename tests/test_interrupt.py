"""Ctrl+C in the terminal closes the window as its close button does; a
KeyboardInterrupt is never a crash to report."""
from __future__ import annotations

import os
import signal
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv import app as app_mod  # noqa: E402


def _qapp():
    return QApplication.instance() or QApplication([])


def test_ctrl_c_closes_the_window_and_a_second_one_ends_at_once():
    qa = _qapp()
    w = QWidget()
    w.show()
    before = signal.getsignal(signal.SIGINT)
    try:
        assert app_mod._close_on_interrupt(w)
        assert w._interrupt_tick.isActive()
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler) and handler is not before
        handler(signal.SIGINT, None)
        for _ in range(5):
            qa.processEvents()
        assert not w.isVisible()
        assert signal.getsignal(signal.SIGINT) is signal.SIG_DFL
    finally:
        signal.signal(signal.SIGINT, before)


def test_a_keyboard_interrupt_is_not_reported_as_a_crash(monkeypatch):
    from dopeiptv.core import report
    sent = []
    monkeypatch.setattr(report, "notify_error", lambda text: sent.append(text))
    saved = (sys.excepthook, sys.unraisablehook)
    import threading
    saved_thread = threading.excepthook
    import faulthandler
    was = faulthandler.is_enabled()
    try:
        app_mod._install_crash_hooks()
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            sys.excepthook(*sys.exc_info())
        assert sent == []
        try:
            raise ValueError("a real one")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        assert len(sent) == 1 and "a real one" in sent[0]
    finally:
        sys.excepthook, sys.unraisablehook = saved
        threading.excepthook = saved_thread
        if not was:
            faulthandler.disable()
