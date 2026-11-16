"""About in Settings (issue #31): a person on Windows looked for About and
the version in Settings, where neither was, and its search found nothing.
General now ends with an "About dopeIPTV" row with the version on it; a
click opens About over Settings, the search finds it by "about", "version"
and "update", and the command palette keeps the one About entry it had
(the menu's). Run on the real dialog in a child process, offscreen, as
test_settings_map."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv import __version__
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell, ToggleSwitch

w = MainWindow(DemoClient(), s, PlaylistStore(s))
calls = []


def fake_about(*args, **kwargs):
    calls.append((args, kwargs))
    s.setValue("nerd_enabled", "true")      # as About's seven taps do


w.show_about = fake_about


def check(self):
    self.show()
    shell = self.findChild(SettingsShell)
    shell.show_page("general")          # built as it shows
    general = next(p for p in shell.pages if p.key == "general")
    rows = [r for c in general.cards for r, _t in c.rows
            if getattr(r, "label", None) is not None and r.label.text() == tr("menu_about")]
    assert len(rows) == 1, len(rows)
    row = rows[0]
    # At the bottom of General, the version muted under its name.
    assert general.cards[-1].rows[-1][0] is row
    assert row.hint_label is not None and __version__ in row.hint_label.text()
    assert row.hint_label.text() == tr("set_about_version", version=__version__)
    assert row.isVisibleTo(general)

    for word in ("version", "about", "update", tr("set_card_updates")):
        shell.search.setText(word)
        assert "general" in shell.search_hits(), (word, shell.search_hits())
        assert not row.isHidden(), word
    shell.search.setText("")

    # The command palette: the menu's About only, no second one from here.
    assert not [e for e in shell.index() if e["name"] == tr("menu_about")]

    switch = next(x for x in self.findChildren(ToggleSwitch)
                  if x.accessibleName() == tr("nerd_switch"))
    assert not switch.isChecked()
    shell.show_page("general")
    row.link.click()
    assert calls == [((), {"parent": self})], calls
    # Nerd turned on in About: the switch follows, so closing Settings
    # does not store it off again.
    assert switch.isChecked()
    self.hide()
    return 0


QDialog.exec = check
w.open_settings()
assert s.value("nerd_enabled") == "true", s.value("nerd_enabled")
print("SETTINGS_ABOUT_OK", flush=True)
os._exit(0)
"""


def test_about_row_on_general_opens_about_and_is_found():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "SETTINGS_ABOUT_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-3000:]
