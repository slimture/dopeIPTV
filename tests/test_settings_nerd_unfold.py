"""Nerd turned on in Settings: its pages come out under NERD and the sidebar
scrolls down to them.

The report: "when Nerd mode is activated, the settings should unfold and
the left menu scroll down so you see all choices". NERD is the last group
of a long sidebar, so its pages came out below the edge of a window of
ordinary height. Turned on, the group is scrolled into sight: its heading
and all its pages when they fit, its last page when they do not. Turned
off, the pages go, Status (with the switch) stays. Opening Settings with
Nerd on shows them all.

The real Settings window in a child process (offscreen), as in
test_settings_window.
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
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell, ToggleSwitch

w = MainWindow(DemoClient(), s, PlaylistStore(s))


def wait(cond, secs=3.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
    return cond()


def in_view(nav, item):
    return (not item.isHidden()
            and nav.viewport().rect().contains(nav.visualItemRect(item)))


def opened(height, check):
    def run(self):
        if height < self.minimumHeight():
            self.setMinimumHeight(0)        # shorter than Settings allows
        self.resize(900, height)
        self.show()
        shell = self.findChild(SettingsShell)
        shell.show_page("nerd")
        wait(lambda: False, 0.05)
        head = shell._section_of("nerd")[0]
        status = shell._items["nerd"]
        kids = [shell._items[k] for k in shell._gated["nerd"]]
        switch = next(x for x in self.findChildren(ToggleSwitch)
                      if x.accessibleName() == tr("nerd_switch"))
        check(shell, shell.nav, head, status, kids, switch)
        self.hide()
        return 0
    QDialog.exec = run
    w.open_settings()


def comes_out_into_view(shell, nav, head, status, kids, switch):
    # Off: NERD and its Status (the switch), nothing else of it.
    assert not switch.isChecked()
    assert not head.isHidden() and not status.isHidden() and all(k.isHidden() for k in kids)
    nav.verticalScrollBar().setValue(0)
    switch.setChecked(True)
    assert wait(lambda: all(not k.isHidden() for k in kids) and shell._into_view is None)
    # It had to scroll, and NERD with every page of it is in sight.
    assert nav.verticalScrollBar().value() > 0
    assert in_view(nav, head) and all(in_view(nav, k) for k in kids), (
        [nav.visualItemRect(x) for x in [head] + kids], nav.viewport().rect())
    # Off: its pages go, Status stays.
    switch.setChecked(False)
    assert all(k.isHidden() for k in kids) and not status.isHidden()


def too_short_for_all(shell, nav, head, status, kids, switch):
    # Not all of the group fits: its last page, at least, is in sight.
    switch.setChecked(True)
    assert wait(lambda: all(not k.isHidden() for k in kids) and shell._into_view is None)
    whole = nav.visualItemRect(kids[-1]).bottom() - nav.visualItemRect(head).top()
    assert whole >= nav.viewport().height(), "the window was meant to be too short"
    assert in_view(nav, kids[-1])


def opened_with_nerd_on(shell, nav, head, status, kids, switch):
    # Nerd on at opening: all its pages are there from the start.
    assert switch.isChecked()
    assert all(not k.isHidden() for k in kids)


opened(600, comes_out_into_view)        # the shortest Settings opens at
s.setValue("nerd_enabled", "false")
opened(250, too_short_for_all)
s.setValue("nerd_enabled", "true")
opened(600, opened_with_nerd_on)
print("NERD_UNFOLD_OK", flush=True)
os._exit(0)
"""


def test_nerd_turned_on_comes_out_and_scrolls_into_view():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "NERD_UNFOLD_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-3000:]
