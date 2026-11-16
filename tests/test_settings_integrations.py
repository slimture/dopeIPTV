"""Settings with and without what a build carries: Simkl's card on the Watch
history page only with a Simkl client id, the Discord card on Integrations
only with Discord's application id, and the Discord switch applied as it is
turned.

The real Settings window in a child process (offscreen), as in
test_settings_window.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
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


def titles(page):
    return [c.property("_title").text().replace(" ", "_") for c in page.cards
            if c.property("_title") is not None]


def run(self):
    self.show()
    shell = self.findChild(SettingsShell)
    shell.build_all()                   # each page is built as it is needed
    seen["pages"] = [p.key for p in shell.pages]
    pages = {p.key: p for p in shell.pages}
    seen["history"] = titles(pages["history"])
    seen["integrations"] = titles(pages["integrations"])
    switch = next((x for x in self.findChildren(ToggleSwitch)
                   if x.accessibleName() == tr("discord_show")), None)
    seen["discord"] = switch is not None
    # Where it is: on Integrations, beside OpenSubtitles.
    if switch is not None:
        assert pages["integrations"].isAncestorOf(switch)
    if switch is not None:
        assert not switch.isChecked()
        switch.setChecked(True)
        seen["on"] = s.value("discord_presence")
    self.hide()
    return 0


QDialog.exec = run
w.open_settings()
print("PAGES", " ".join(seen["pages"]))
print("HISTORY", " ".join(seen["history"]))
print("INTEGRATIONS", " ".join(seen["integrations"]))
print("DISCORD", seen["discord"], seen.get("on"))
print("SIMKL", w.simkl.available(), flush=True)   # os._exit flushes nothing
os._exit(0)
"""


def _settings(app_id: str, simkl_id: str) -> str:
    # A config directory of its own, and a second try, as test_settings_window
    # has: on the CI runner the whole window has been seen to die once
    # without a word from Python.
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               XDG_CONFIG_HOME=tempfile.mkdtemp())
    env.pop("DOPEIPTV_SIMKL_CLIENT_ID", None)
    if simkl_id:
        env["DOPEIPTV_SIMKL_CLIENT_ID"] = simkl_id
    for _attempt in (1, 2):
        out = subprocess.run([sys.executable, "-c", _CHILD, app_id], cwd=_REPO_ROOT,
                             env=env, capture_output=True, text=True, timeout=120)
        if "PAGES" in out.stdout:
            return out.stdout
    raise AssertionError(f"exit {out.returncode}\nstdout={out.stdout!r}\n"
                         f"stderr={out.stderr[-3000:]!r}")


def _line(out: str, head: str) -> list[str]:
    return out.split(head + " ", 1)[1].split("\n", 1)[0].split()


def test_what_the_build_carries_is_what_settings_offers():
    with_both = _settings("1234567890", "client-id")
    # One page for the watch history: Trakt's card, Simkl's, and the one
    # history they fill together - no page of Simkl's own.
    assert _line(with_both, "HISTORY") == ["Trakt", "Simkl", "Watched_history"]
    assert "simkl" not in _line(with_both, "PAGES")
    assert _line(with_both, "INTEGRATIONS") == ["OpenSubtitles.com", "Discord", "radio-browser.info"]
    assert "DISCORD True true" in with_both and "SIMKL True" in with_both


def test_without_the_ids_neither_is_offered():
    out = _settings("", "")
    if "SIMKL True" in out:
        return              # a checkout with a _simkl_key.py of its own
    assert _line(out, "HISTORY") == ["Trakt", "Watched_history"]
    assert _line(out, "INTEGRATIONS") == ["OpenSubtitles.com", "radio-browser.info"]
    assert "DISCORD False" in out
