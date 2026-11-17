"""Where each setting is: the Settings sidebar in its groups, every row on
one page of one group, and the search reaching all of them.

The pages are grouped by what a person is doing (AGENTS.md, "Where a
setting goes"; the map is docs/SETTINGS_MAP.md): WATCH, CONTENT, ACCOUNTS
AND INTEGRATIONS, SYSTEM and - in its own colour - NERD, whose pages but
Status show only while Nerd is on. This checks, on the real dialog in a
child process (offscreen, as test_settings_window):

- each page sits under exactly one group heading, the groups as listed;
- each row is on exactly one page, and each control in the pages is in a row;
- no page is a single stray row (Casting excepted: AirPlay joins it);
- a search finds a row of every page, and says which page it is on;
- Nerd's pages, and the rows on other pages that lead to them, show only
  while Nerd is on;
- the map in docs/SETTINGS_MAP.md names every page.
"""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QDialog,
                             QLineEdit, QPlainTextEdit, QPushButton)
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import Chip, SettingsPage, SettingsShell, ToggleSwitch

w = MainWindow(DemoClient(), s, PlaylistStore(s))
GROUPS = [
    ("set_grp_watch", ["playback", "audio", "guide", "multiview", "recording", "casting"]),
    ("set_grp_content", ["playlists", "mine", "local", "metadata"]),
    ("set_grp_accounts", ["history", "sync", "integrations"]),
    ("set_sec_system", ["general", "appearance", "shortcuts", "profiles", "parental", "advanced"]),
    ("nerd_page", ["nerd", "nerd_net", "nerd_boxes", "nerd_player", "nerd_serve", "nerd_diag"]),
]
NERD_ONLY = GROUPS[-1][1][1:]
SINGLE_ROW_OK = {"casting"}
doc = open(os.path.join(os.environ["REPO"], "docs", "SETTINGS_MAP.md"), encoding="utf-8").read()


def page_of(widget):
    while widget is not None and not isinstance(widget, SettingsPage):
        widget = widget.parentWidget()
    return widget


def wait(cond, secs=3.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
    return cond()


def check(self):
    self.show()
    shell = self.findChild(SettingsShell)
    shell.build_all()                   # each page is built as it is needed
    nav = shell.nav
    pages = {p.key: p for p in shell.pages}
    # The rows that lead to Nerd's pages (General's About leads to a window).
    links = [b for b in self.findChildren(QPushButton) if b.objectName() == "SettingsLink"
             and b.text().startswith(tr("nerd_page"))]
    switch = next(x for x in self.findChildren(ToggleSwitch) if x.accessibleName() == tr("nerd_switch"))

    # -- Nerd off: its pages, and the rows that lead to them, are not there.
    assert not switch.isChecked()
    assert shell._off == set(NERD_ONLY), shell._off
    assert all(shell._items[k].isHidden() for k in NERD_ONLY)
    assert not shell._items["nerd"].isHidden()
    assert links and not any(b.isVisibleTo(page_of(b)) for b in links)
    shell.search.setText(tr("nerd_probe"))           # a row on Nerd > Player
    assert "nerd_player" not in shell.search_hits(), shell.search_hits()
    shell.search.setText("")
    switch.setChecked(True)
    assert wait(lambda: not any(shell._items[k].isHidden() for k in NERD_ONLY))
    assert all(b.isVisibleTo(page_of(b)) for b in links)
    shell.search.setText(tr("nerd_probe"))
    assert "nerd_player" in shell.search_hits(), shell.search_hits()
    shell.search.setText("")

    # -- each page under exactly one heading, in the groups as listed.
    under, heading, seen_heads = {}, None, []
    for i in range(nav.count()):
        it = nav.item(i)
        key = it.data(0x0100)
        if not key:
            heading = it.text()
            seen_heads.append(heading)
            continue
        assert heading is not None, f"{key} is under no heading"
        assert key not in under, f"{key} twice in the sidebar"
        under[key] = heading
    assert seen_heads == [tr(g).upper() for g, _keys in GROUPS], seen_heads
    want = {k: tr(g).upper() for g, keys in GROUPS for k in keys}
    assert under == want, (under, want)
    assert set(under) == set(pages), set(pages) ^ set(under)

    # -- each row on exactly one page; each control in a row.
    owner = {}
    for key, page in pages.items():
        for card in page.cards:
            assert page.isAncestorOf(card), (key, "a card off its page")
            for row, _text in card.rows:
                assert id(row) not in owner, (key, owner.get(id(row)))
                assert page.isAncestorOf(row), (key, "a row off its page")
                owner[id(row)] = key
        rows = sum(len(c.rows) for c in page.cards)
        assert rows >= 2 or key in SINGLE_ROW_OK, (key, rows, "a page with a single stray row")
    controls = [x for x in shell.stack.findChildren((ToggleSwitch, QComboBox, Chip, QPlainTextEdit, QLineEdit))
                if not isinstance(x.parent(), (QAbstractSpinBox, QComboBox))]
    assert len(controls) > 100, len(controls)
    for x in controls:
        up = x
        while up is not None and id(up) not in owner:
            up = up.parentWidget()
        assert up is not None, (type(x).__name__, x.accessibleName() or x.objectName(), "in no row")

    # -- a search finds a row of every page, and says where it is.
    for key, page in pages.items():
        label = next((row.label.text() for c in page.cards for row, _t in c.rows
                      if getattr(row, "label", None) is not None and row.label.text().strip()
                      and row.isVisibleTo(page)), None)
        assert label, (key, "no row with a label showing")
        shell.search.setText(label)
        assert key in shell.search_hits(), (key, label, shell.search_hits())
        where = page.crumb if page.crumb is not None else page.path
        assert where is not None and where.isVisibleTo(shell.results), key
        assert under[key].casefold().startswith(where.text().split()[0].casefold()), (key, where.text())
        shell.search.setText("")
        assert page.path is None or not page.path.isVisibleTo(page)

    # -- the map names every page.
    for key in pages:
        assert f"`{key}`" in doc, f"docs/SETTINGS_MAP.md does not name the page {key}"
    self.hide()
    return 0


QDialog.exec = check
w.open_settings()
print("SETTINGS_MAP_OK", flush=True)
os._exit(0)
"""


def test_every_setting_has_one_page_in_one_group_and_is_found():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", REPO=_REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "SETTINGS_MAP_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-3000:]
