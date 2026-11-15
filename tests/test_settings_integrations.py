"""Settings with and without what a build carries: the Simkl page only with
a Simkl client id, the Discord card only with Discord's application id, and
the Discord switch applied as it is turned.

The real Settings window in a child process (offscreen), as in
test_settings_window.
"""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core import discord_rpc
discord_rpc.DISCORD_APP_ID = sys.argv[1]
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell, ToggleSwitch

w = MainWindow(DemoClient(), s, PlaylistStore(s))
seen = {}


def run(self):
    self.show()
    shell = self.findChild(SettingsShell)
    seen["pages"] = [p.key for p in shell.pages]
    switch = next((x for x in self.findChildren(ToggleSwitch)
                   if x.accessibleName() == tr("discord_show")), None)
    seen["discord"] = switch is not None
    if switch is not None:
        assert not switch.isChecked()
        switch.setChecked(True)
        seen["on"] = s.value("discord_presence")
    self.hide()
    return 0


QDialog.exec = run
w.open_settings()
print("PAGES", " ".join(seen["pages"]))
print("DISCORD", seen["discord"], seen.get("on"))
print("SIMKL", w.simkl.available())
os._exit(0)
"""


def _settings(app_id: str, simkl_id: str) -> str:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("DOPEIPTV_SIMKL_CLIENT_ID", None)
    if simkl_id:
        env["DOPEIPTV_SIMKL_CLIENT_ID"] = simkl_id
    out = subprocess.run([sys.executable, "-c", _CHILD, app_id], cwd=_REPO_ROOT,
                         env=env, capture_output=True, text=True, timeout=120)
    assert "PAGES" in out.stdout, out.stdout + out.stderr
    return out.stdout


def test_what_the_build_carries_is_what_settings_offers():
    with_both = _settings("1234567890", "client-id")
    assert "simkl" in with_both.split("PAGES", 1)[1].split("\n", 1)[0].split()
    assert "DISCORD True true" in with_both and "SIMKL True" in with_both


def test_without_the_ids_neither_is_offered():
    out = _settings("", "")
    if "SIMKL True" in out:
        return              # a checkout with a _simkl_key.py of its own
    assert "simkl" not in out.split("PAGES", 1)[1].split("\n", 1)[0].split()
    assert "DISCORD False" in out
