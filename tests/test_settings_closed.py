"""A closed Settings window does not stay for the rest of the session.

Each was kept as a child of the main window and never deleted: some 2300
widgets every opening (the command palette's hidden one for its index
too), all restyled at every theme change - measured offscreen, a theme
change went from 0.04 s to 1.6 s after one Settings opened and 9 s after
five. A closed window is now deleted a while after it closes
(SETTINGS_KEEP_MS), and an answer still on its way to one of its rows (the
account looked up as every window opens) finds it gone without an error.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import json, os, sys, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
tmp = tempfile.mkdtemp()
for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config"),
                 ("XDG_DATA_HOME", "data")):
    os.environ[var] = os.path.join(tmp, sub)
from PyQt6.QtCore import QSettings
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([
    {"id": "x", "name": "Xtream", "kind": "xtream", "server": "http://x.invalid",
     "username": "u", "password": "p"}]))
s.setValue("active_playlist", "x")
import traceback
errors = []


def hook(t, v, tb):
    # What a slot raises (PyQt hands it here) - and printed, for this test.
    errors.append(f"{t.__name__}: {v}")
    traceback.print_exception(t, v, tb)


sys.excepthook = hook
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import mw_settings
from dopeiptv.ui.main_window import MainWindow

gate = threading.Event()
asked = []


class Slow:
    # The account behind the source: answered only once the windows are gone.
    def account_info(self):
        asked.append(1)
        gate.wait(30)
        return {"user_info": {"status": "Active"}}


mw_settings.make_client = lambda pl: Slow()
w = MainWindow(DemoClient(), s, PlaylistStore(s))
QDialog.exec = lambda self: 0          # each window closes at once


def kept():
    return [x for x in w.findChildren(QDialog) if x.windowTitle() == tr("settings_title")]


w.open_settings()
QTest.qWait(30)
# Right after it closed it is still there, for an answer on its way.
first = kept()
assert len(first) == 1, len(first)
MainWindow.SETTINGS_KEEP_MS = 20       # for these, their minute gone by
for _ in range(3):
    w.open_settings()
    QTest.qWait(30)
w.open_settings(index_only=True)
end = time.monotonic() + 10
while time.monotonic() < end and len(kept()) > 1:
    QTest.qWait(30)
# Only the first is left (its minute is not over); every other is gone.
assert kept() == first, len(kept())
gate.set()
end = time.monotonic() + 15
while time.monotonic() < end and len(asked) < 5:
    QTest.qWait(30)
QTest.qWait(500)
assert len(asked) == 5, asked
assert not errors, errors
print("CLOSED_OK", flush=True)
"""


def test_a_closed_settings_window_goes_and_late_answers_find_it_gone(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True,
                          text=True, env=env, cwd=_REPO_ROOT, timeout=240)
    assert "CLOSED_OK" in proc.stdout, (
        f"stdout={proc.stdout[-2000:]!r}\nstderr={proc.stderr[-4000:]!r}")
