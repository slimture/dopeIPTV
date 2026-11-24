"""The ways to Settings from where its settings matter (ui/mw_settings.py:
PAGE_TITLES, settings_link_text, add_settings_link): the player's options
menu, the guide's menu, Recordings' ⋯, Local files' menu, a multiview cell's
menu and the cast window each lead to the page their settings are on - a
page that exists, under the title it has there."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog, QMenu
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import mw_settings
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell

w = MainWindow(DemoClient(), s, PlaylistStore(s))

# Every page a link names is a page of Settings, under the title it shows.
seen = {}
def pages(self):
    shell = self.findChild(SettingsShell)
    seen.update({p.key: p.title for p in shell.pages})
    return 0
QDialog.exec = pages
w.open_settings()
for key, title_key in mw_settings.PAGE_TITLES.items():
    assert seen.get(key) == tr(title_key), (key, seen.get(key), tr(title_key))
assert w.settings_link_text("audio") == tr("settings_title") + " › " + tr("set_page_av") + "…"

# A link opens Settings on its page once the menu is gone.
opened = []
w.open_settings = lambda *a, at_page=None, **k: opened.append(at_page)
m = QMenu()
act = w.add_settings_link(m, "recording")
assert act.text() == w.settings_link_text("recording")
act.trigger()
assert opened == []                       # not inside the menu's own event
end = time.monotonic() + 2
while not opened and time.monotonic() < end:
    app.processEvents()
assert opened == ["recording"], opened

# The player's options menu ends with the way to Picture & sound.
if w.player is not None:
    m = QMenu()
    w.player.populate_options_menu(m)
    texts = [a.text() for a in m.actions()]
    assert texts[-1] == w.settings_link_text("audio"), texts[-4:]

# The guide's menu, over an empty spot of the board, ends with Guide's page.
from dopeiptv.ui.epg_grid import EpgGrid
shown = []
import dopeiptv.ui.epg_grid as eg
eg.popup_menu_over_video = lambda menu, pos: shown.append([a.text() for a in menu.actions()])
grid = EpgGrid(w, [])
grid._block_at = lambda _p: None
from PyQt6.QtCore import QPoint, QPointF
grid._context_at(QPointF(0, 0), QPoint(0, 0))
assert shown and shown[-1][-1] == w.settings_link_text("guide"), shown

# Recordings' ⋯ menu ends with Recording's page.
m = QMenu()
w._rp_page = None
w._rp_fill_more(m)
assert m.actions()[-1].text() == w.settings_link_text("recording")
print("SETTINGS_LINKS_OK", flush=True)
os._exit(0)
"""


def test_each_menu_leads_to_the_settings_page_its_settings_are_on():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "SETTINGS_LINKS_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-3000:]
