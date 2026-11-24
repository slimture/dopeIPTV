"""What the performance budget tests share (docs/PERFORMANCE.md): a child
process with a window of its own, offscreen, its folders its own, that
prints one line of JSON; and the longer local mode.

A window runs in a child process, as in the other window tests (the main
window owns a GL widget, see test_robustness): its numbers are its own, not
the test session's, and a budget broken there fails here with them.

``DOPEIPTV_PERF_LONG=1`` runs each measurement longer - more zaps, more
place switches, a full week of a big guide - for a person looking into a
change; CI runs the short one."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The longer local run.
LONG = os.environ.get("DOPEIPTV_PERF_LONG", "").strip() not in ("", "0")

#: The start of every window child: Qt offscreen, settings of its own, a
#: window on the demo source, shown, and helpers to wait and pump.
PRELUDE = r"""
import json, os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEventLoop, QSettings, QTimer
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])


def wait(sec):
    loop = QEventLoop()
    QTimer.singleShot(int(sec * 1000), loop.quit)
    loop.exec()


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def done(result):
    print("RESULT" + json.dumps(result), flush=True)
    os._exit(0)
"""


def run_child(code: str, timeout: float = 300.0, env: dict | None = None) -> dict:
    """Run *code* (after PRELUDE) in a child process; what it gave done()."""
    home = tempfile.mkdtemp(prefix="perf-child-")
    run_env = dict(os.environ, QT_QPA_PLATFORM="offscreen", **(env or {}))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        path = os.path.join(home, var.lower())
        os.makedirs(path, exist_ok=True)
        run_env[var] = path
    out = subprocess.run([sys.executable, "-c", PRELUDE + code], cwd=ROOT, env=run_env,
                         capture_output=True, text=True, timeout=timeout)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, f"exit {out.returncode}\n{out.stdout[-3000:]}\n{out.stderr[-4000:]}"
    return json.loads(line[6:])
