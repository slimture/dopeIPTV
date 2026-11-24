"""The start: the import chain to a shown window, and what it leaves out.

The window opens before anything is fetched (app.py's main()), and what
is imported before its first frame is waited for by every start. What is
needed only on the first request, a cast or a picture's decode belongs
after it: requests with urllib3 and certifi (core/_lazy_requests.py),
pychromecast and zeroconf (providers/chromecast.py)... mpv is on the chain
on purpose: the docked player is made with the window.

A child process starts as the app does - the import chain, a source's
client made, the window built and shown - and says what it had imported
by its first frame, and how long each part took. The budgets are about
three times what this took when they were set (docs/PERFORMANCE.md); the
times are the better of two starts, the first of which may compile every
module.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests import perf

#: Never imported by the first frame: each is on the first network call,
#: cast or picture's way, after the window is up.
HEAVY = ("requests", "urllib3", "charset_normalizer", "chardet", "certifi", "idna",
         "PIL", "numpy", "pychromecast", "zeroconf", "ifaddr", "google.protobuf",
         "casttube", "yt_dlp", "cryptography", "OpenSSL")

#: Seconds, the better of two starts. Measured on the machine the budgets
#: were set on (the most of five): the import chain 0.39, the window built
#: 0.10, from show() to its first frame 0.02, from the start of the process
#: 0.60 (0.75 before a source's client stopped making its requests session
#: at once: making three clients took 0.05 s, now 0.005). The frame's is
#: left looser than three times: one collection or a slow paint on a shared
#: runner is a few hundredths of a second.
IMPORT_S = 1.2
WINDOW_S = 0.3
FRAME_S = 0.25
TOTAL_S = 1.8
#: Modules imported by the first frame: measured 491 in all, 279 of them
#: the app's own (620 in all before: requests and what it brings were 129).
#: About a fifth more than that - a Python or a PyQt6 of another version
#: imports a few more or fewer of its own; one more library chain does not
#: fit.
MODULES = 600
OWN_MODULES = 340

_CHILD = r"""
import os, sys, time
t0 = time.perf_counter()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
from PyQt6.QtCore import QEvent, QObject, QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
t1 = time.perf_counter()
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers import client as clients
from dopeiptv.ui import main_window as mw
t2 = time.perf_counter()
# Each kind of source the app opens on most, made as app.py makes it
# before the window: nothing of the network is needed yet.
for pl in ({"id": "a", "kind": "xtream", "server": "http://192.0.2.1", "username": "uu1",
            "password": "pp1"},
           {"id": "b", "kind": "m3u", "server": "http://192.0.2.1/list.m3u"},
           {"id": "c", "kind": "stalker", "server": "http://192.0.2.1/c/",
            "mac": "00:1A:79:00:00:01"}):
    clients.make_client(pl)
t_clients = time.perf_counter() - t2
made = sorted(m for m in ("requests", "urllib3") if m in sys.modules)
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
t3 = time.perf_counter()
w = mw.MainWindow(clients.DemoClient(), s, PlaylistStore(s))
t4 = time.perf_counter()


class First(QObject):
    at = None

    def eventFilter(self, obj, ev):
        if self.at is None and ev.type() == QEvent.Type.Paint:
            try:
                mine = obj.window() is w
            except Exception:
                mine = False
            if mine:
                self.at = time.perf_counter()
        return False


first = First()
app.installEventFilter(first)
w.show()
deadline = time.perf_counter() + 10
while first.at is None and time.perf_counter() < deadline:
    app.processEvents()
t5 = first.at or time.perf_counter()
mods = list(sys.modules)
import json
print("RESULT" + json.dumps({
    "made": made, "modules": len(mods),
    "own": sum(1 for m in mods if m == "dopeiptv" or m.startswith("dopeiptv.")),
    "heavy": sorted(set(mods) & set(sys.argv[1].split(","))),
    "import": t2 - t1, "clients": t_clients, "window": t4 - t3, "frame": t5 - t4, "total": t5 - t0,
    "painted": first.at is not None}), flush=True)
os._exit(0)
"""


def _start() -> dict:
    import tempfile
    home = tempfile.mkdtemp(prefix="perf-start-")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = os.path.join(home, var.lower())
    out = subprocess.run([sys.executable, "-c", _CHILD, ",".join(HEAVY)], cwd=perf.ROOT,
                         env=env, capture_output=True, text=True, timeout=300)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, out.stdout[-2000:] + out.stderr[-4000:]
    return json.loads(line[6:])


@pytest.fixture(scope="module")
def starts():
    pytest.importorskip("PyQt6.QtWidgets")
    return [_start() for _ in range(4 if perf.LONG else 2)]


def test_heavy_modules_stay_off_the_start(starts):
    for got in starts:
        assert got["made"] == [], ("a source's client made before the window imported "
                                   "requests: its session is made on its first use", got)
        assert got["heavy"] == [], got
        assert got["painted"], got


def test_the_start_imports_within_its_budget(starts):
    got = starts[-1]
    assert got["modules"] <= MODULES, got
    assert got["own"] <= OWN_MODULES, got


@pytest.mark.parametrize("part,budget", [("import", IMPORT_S), ("window", WINDOW_S),
                                         ("frame", FRAME_S), ("total", TOTAL_S)])
def test_the_start_within_its_time(starts, part, budget):
    best = min(got[part] for got in starts)
    assert best <= budget, (part, [round(got[part], 3) for got in starts])
