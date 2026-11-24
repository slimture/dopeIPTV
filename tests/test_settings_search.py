"""Settings' search: every word typed in a row, its section's heading or
its page's name, folded as the command palette folds (case, accents and
punctuation left out), and a row found by its English words in every
language (ui/settings_ui.py: findable, all_found)."""
from __future__ import annotations

import os
import subprocess
import sys

from dopeiptv import i18n
from dopeiptv.ui.settings_ui import all_found, findable

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_a_row_is_found_by_its_words_folded_and_by_its_english():
    i18n.set_language("sv")
    try:
        night = findable(i18n.tr("night_mode"), i18n.tr("night_mode_hint"))
        assert all_found(["nattlage"], night)              # no accent needed
        assert all_found(["night", "mode"], night)         # its English too
        assert all_found(["mode", "night"], night)         # in any order
        assert not all_found(["night", "storlek"], night)  # every word, not one
        # A word in the section's heading or the page's name counts for the row.
        sharpen = findable(i18n.tr("setting_sharpen"))
        heading = findable(i18n.tr("sec_video"))
        assert not all_found(["video", "skarpa"], sharpen)
        assert all_found(["video", "skarpa"], sharpen, heading)
        # A name with no string of its own is found as it is.
        assert all_found(["vu", "solo4k"], findable("VU+ Solo4K"))
    finally:
        i18n.set_language("en")
    # In English nothing is added: the row's own words.
    assert findable(i18n.tr("night_mode")) == " night mode "


_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv import i18n
i18n.set_language("sv")
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell

w = MainWindow(DemoClient(), s, PlaylistStore(s))


def shown(shell, key, label):
    page = next(p for p in shell.pages if p.key == key)
    return [r for c in page.cards for r, _t in c.rows
            if getattr(r, "label", None) is not None and r.label.text() == label
            and r.isVisibleTo(shell.results)]


def check(self):
    shell = self.findChild(SettingsShell)
    shell.build_all()
    cases = [
        # two words in either order, and one in the row, one in its heading
        ("undertext storlek", "audio", tr("setting_sub_size")),
        ("storlek undertext", "audio", tr("setting_sub_size")),
        ("video skarpa", "audio", tr("setting_sharpen")),
        # no accent, and the row's English in a Swedish window
        ("nattlage", "audio", tr("night_mode")),
        ("night mode", "audio", tr("night_mode")),
        ("sakerhetskopia losenord", "privacy", tr("set_backup_pw")),
        ("mobil fjarrkontroll", "tv", tr("phone_title")),
    ]
    for query, key, label in cases:
        shell.search.setText(query)
        assert key in shell.search_hits(), (query, shell.search_hits())
        assert shown(shell, key, label), (query, key, label)
    shell.search.setText("xyzzy plugh")
    assert shell.search_hits() == []
    shell.search.setText("")
    return 0


QDialog.exec = check
w.open_settings()
print("SETTINGS_SEARCH_OK", flush=True)
os._exit(0)
"""


def test_the_settings_window_finds_rows_in_swedish_by_any_of_their_words():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "SETTINGS_SEARCH_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-3000:]
