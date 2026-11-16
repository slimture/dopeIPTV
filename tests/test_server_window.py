"""Server mode in the window: off by default and nothing on the player then;
on (with Nerd) it listens, hands the player its hook, says how it stands in
Nerd > Diagnostics and on its Settings line, gives way to the app's own use
and stops cleanly - in a child process, offscreen, with settings of its own."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import json, os, socket, tempfile, urllib.request
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow

w = MainWindow(DemoClient(), s, PlaylistStore(s))
p = w.player
hooked = callable(getattr(p, "dvr_pool_for_server", None))

# Off by default: nothing runs, nothing is on the player.
w._server_apply()
assert w._home is None and getattr(p, "live_hook", None) is None
assert not w._server_state()["running"]

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
sock.close()
for k, v in (("nerd_enabled", "true"), ("nerd_server", "true"), ("nerd_server_port", str(port))):
    s.setValue(k, v)
st = w._server_apply()
assert st["running"] and st["port"] == port, st
if hooked:
    assert p.live_hook == w._server_live_hook
    assert p._dvr_pool is not None and p._dvr_pool is w._home_host.kept_pool
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url):
    # A request from another device, while the window's thread runs: the
    # server asks the window for the page on that thread.
    import threading, time
    got = {}

    def run():
        try:
            r = opener.open(url, timeout=10)
            got["r"] = (r.status, dict(r.headers), r.read())
        except Exception as e:
            got["e"] = e
    t = threading.Thread(target=run)
    t.start()
    end = time.monotonic() + 15
    while t.is_alive() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    if "e" in got:
        raise got["e"]
    return got["r"]


status, _h, body = fetch(f"http://127.0.0.1:{port}/discover.json")
assert json.loads(body)["FriendlyName"] == "dopeIPTV"
status, headers, body = fetch(st["addresses"]["page"].replace(st["addresses"]["tuner"],
                                                              f"http://127.0.0.1:{port}"))
assert status == 200 and "script-src" in headers["Content-Security-Policy"]
status, _h, body = fetch(f"http://127.0.0.1:{port}/api/settings?token=" + str(s.value("nerd_server_token")))
assert json.loads(body)["port"] == port
lines = w._in_use_lines(ask=False)
assert any("dopeIPTV" in text for _kind, text in lines), lines
w._server_make_room()                       # nothing to let go: no error
from dopeiptv.services.home_server import check_server
ck = check_server(st["on"], st["running"], st["port"], st["error"], st["status"], st["addresses"])
assert ck.state == "ok", ck
assert s.value("nerd_server_token") and len(str(s.value("nerd_server_device_id"))) == 8

# Nerd off: the server goes, and the player's hook with it.
s.setValue("nerd_enabled", "false")
w._server_apply()
assert w._home is None and getattr(p, "live_hook", None) is None
s.setValue("nerd_enabled", "true")
w._server_apply()
assert w._home is not None
w._server_stop(wait=True)
assert getattr(p, "live_hook", None) is None
print("OK")
"""


def test_server_mode_in_the_window():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    assert "OK" in out.stdout, out.stdout[-3000:] + out.stderr[-5000:]
