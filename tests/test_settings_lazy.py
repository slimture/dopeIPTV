"""Settings builds the page it opens on, every other the first time it is
needed - and shows nothing as a window of its own on the way.

On GNOME (Wayland) the pointer spun for some 25 s after Settings opened:
each "This profile's" mark (and Profiles' "In use") was shown before it had
a row, a window of its own for a moment, whose activation token GNOME waited
on until it timed out. And every page was built and polished as the window
opened, some 2300 widgets, before it answered the compositor at all.

- The shell (in process): a page is built when shown, the pages sharing a
  builder together, each told to ``page_built`` once; a search builds the
  pages it reaches (not those left out) and finds their rows; a filter asked
  for while pages are built runs once they are; a builder that fails leaves
  the rest of Settings working.
- The real window (a child process, as test_settings_window), Nerd on, two
  profiles: it opens with one page built; no widget shows as a window of its
  own while it opens, while every page is visited, searched, a source's page
  opened; a page built later stores what its controls say as they change,
  one never built stores nothing; the search finds a row on a page not
  built; a link row leads to a Nerd page not built; the profile's marks show
  on a page built later; the light-theme row follows its switch; About's
  seven taps bring Nerd's pages out with them not built; a closed window is
  freed.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout  # noqa: E402

from dopeiptv.ui.settings_ui import SettingsPage, SettingsShell, page_icon  # noqa: E402

app = QApplication.instance() or QApplication([])
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _blank(_kind, size, _colour):
    return QPixmap(size, size)


def _shell():
    d = QDialog()
    QVBoxLayout(d).addWidget(shell := SettingsShell("Search", "none"))
    built: list[str] = []
    told: list[str] = []
    pages = {}

    def add(key: str, title: str) -> SettingsPage:
        pages[key] = shell.add_page(SettingsPage(key, title, ""), page_icon("gear", _blank))
        return pages[key]

    for key in ("one", "two", "pair_a", "pair_b", "off", "broken"):
        add(key, key.title())

    def builder(key: str, row: str):
        def build() -> None:
            built.append(key)
            pages[key].card("Card").row(row, QPushButton("x"))
        return build

    pages["one"].set_builder(builder("one", "Alpha row"))
    pages["two"].set_builder(builder("two", "Bravo row"))

    def pair() -> None:
        built.append("pair")
        pages["pair_a"].card().row("Charlie row")
        pages["pair_b"].card().row("Delta row")
        # As Nerd's build does: its group turned on or off - a filter.
        shell.set_group_enabled("g", False)
    pages["pair_a"].set_builder(pair)
    pages["pair_b"].set_builder(pair)
    pages["off"].set_builder(builder("off", "Echo row"))

    def broken() -> None:
        built.append("broken")
        pages["broken"].card().row("Foxtrot row")
        raise RuntimeError("a builder that fails")
    pages["broken"].set_builder(broken)
    shell.set_gated("g", ["off"])
    shell.arrange([("Group", ["one", "two", "pair_a", "pair_b", "broken"]), ("G", ["off"])])
    shell.page_built = lambda p: told.append(p.key)
    shell.animate = False
    shell.show_page("one")
    return d, shell, pages, built, told


def test_the_shell_builds_a_page_as_it_is_needed():
    d, shell, pages, built, told = _shell()
    assert built == [] and not any(p.built for k, p in pages.items())
    shell.ready()
    assert built == ["one"] and told == ["one"]
    d.resize(900, 600)
    d.show()
    app.processEvents()
    # Shown: built before it shows, once.
    shell.show_page("two")
    assert built == ["one", "two"] and shell.current_key() == "two"
    shell.show_page("one")
    shell.show_page("two")
    assert built == ["one", "two"]
    # The pages sharing a builder are built together, each told once; the
    # filter it asked for ran once it was done.
    shell.show_page("pair_b")
    assert built.count("pair") == 1 and pages["pair_a"].built and pages["pair_b"].built
    assert told == ["one", "two", "pair_a", "pair_b"]
    assert not shell._refilter and shell.current_key() == "pair_b"
    assert shell.stack.currentWidget() is pages["pair_b"]
    # A search reaches the rest, not a page left out; what it finds shows.
    assert "off" in shell._off
    shell.search.setText("foxtrot")
    assert pages["broken"].built and not pages["off"].built
    assert shell.search_hits() == ["broken"]       # what it had before it failed
    shell.search.setText("bravo")
    assert shell.search_hits() == ["two"]
    shell.search.setText("")
    assert not shell.complete()
    # Everything, for the command palette's index: the page left out too.
    shell.build_all()
    assert shell.complete() and built.count("off") == 1
    assert {r["name"] for r in shell.index()} >= {"Alpha row", "Echo row", "Delta row"}
    assert told.count("pair_a") == 1 and told.count("off") == 1
    d.close()


_CHILD = r"""
import json, os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
tmp = tempfile.mkdtemp()
for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config"),
                 ("XDG_DATA_HOME", "data")):
    os.environ[var] = os.path.join(tmp, sub)
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QSettings, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton, QWidget
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
path = os.path.join(tmp, "s.ini")
s0 = QSettings(path, QSettings.Format.IniFormat)
s0.setValue("nerd_enabled", "true")
s0.setValue("playlists", json.dumps([
    {"id": "x", "name": "Xtream", "kind": "xtream", "server": "http://x.invalid",
     "username": "u", "password": "p"},
    {"id": "e", "name": "Box", "kind": "enigma2", "server": "http://192.0.2.10"}]))
s0.setValue("active_playlist", "x")
s0.sync()
del s0
from dopeiptv import i18n
i18n.set_language("en")
from dopeiptv.core import nerd, profiles
from dopeiptv.core.profiles import ProfileSettings
s = ProfileSettings(path, QSettings.Format.IniFormat)
reg = profiles.start(s, [], "Me")
reg.add("Kid", kids=True)
nerd.install(s)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell, ToggleSwitch
from dopeiptv.ui.source_page import Origin

stray = []
main = []


class Watch(QObject):
    # A widget shown as a window of its own: on GNOME (Wayland) an
    # activation token asked for and never used - the pointer spins.
    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                and obj.isWindow() and obj not in main
                and obj.windowType() not in (Qt.WindowType.Popup, Qt.WindowType.ToolTip,
                                             Qt.WindowType.Dialog)):
            stray.append(f"{type(obj).__name__} {getattr(obj, 'text', lambda: '')()!r}")
        return False


w = MainWindow(DemoClient(), s, PlaylistStore(s))
main.append(w)
assert w._profile_many()
w.resize(1200, 760)
w.show()
app.processEvents()
watch = Watch()
app.installEventFilter(watch)


def wait(cond, secs=4.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def page(shell, key):
    return next(p for p in shell.pages if p.key == key)


def switch_named(dlg, name):
    return next(x for x in dlg.findChildren(ToggleSwitch) if x.accessibleName() == name)


kept = []

# 1. Opened: the page it opens on alone; a page built later stores what its
# controls say as they change; the light-theme row follows its switch; the
# profile's marks show on a page built later; one never built stores nothing.
def first(self):
    kept.append(self)
    self.show()
    app.processEvents()
    shell = self.findChild(SettingsShell)
    assert [p.key for p in shell.pages if p.built] == ["playback"], [p.key for p in shell.pages if p.built]
    assert len(self.findChildren(QWidget)) < 900, len(self.findChildren(QWidget))
    shell.show_page("audio")
    assert page(shell, "audio").built
    deint = switch_named(self, tr("setting_deinterlace"))
    was = s.value("video_deinterlace")
    deint.setChecked(not deint.isChecked())
    assert wait(lambda: s.value("video_deinterlace") not in (None, was)), s.value("video_deinterlace")
    shell.show_page("appearance")
    look = page(shell, "appearance")
    marks = [m for m in look.findChildren(Origin) if m.text() == tr("profile_own")]
    shown = [m for m in marks if m.parentWidget().isVisibleTo(look)]   # rows on show
    assert len(shown) >= 3 and all(m.isVisibleTo(look) for m in shown), (len(marks), len(shown))
    follow = switch_named(self, tr("setting_follow_system"))
    light = next(r for c in look.cards for r, _t in c.rows
                 if getattr(r, "label", None) is not None and r.label.text() == tr("setting_light_theme"))
    assert not light.isVisibleTo(look)
    follow.setChecked(True)
    assert light.isVisibleTo(look)
    follow.setChecked(False)
    for key in [p.key for p in shell.pages]:
        shell.show_page(key)                       # every page, as it is visited
        app.processEvents()
    shell.search.setText("a")
    app.processEvents()
    shell.search.setText("")
    self.hide()
    return 0


w.SETTINGS_KEEP_MS = 50
QDialog.exec = first
w.open_settings()
assert s.value("theme_follow_system") == "false"

# 2. The search finds a row on a page not built; a link row leads to a Nerd
# page not built.
def search_and_link(self):
    self.show()
    shell = self.findChild(SettingsShell)
    assert not page(shell, "multiview").built
    shell.search.setText(tr("mv_set_seek_step"))
    assert shell.search_hits() == ["multiview"], shell.search_hits()
    shell.search.setText("")
    self.hide()
    return 0


def link(self):
    self.show()
    shell = self.findChild(SettingsShell)
    shell.show_page("playback")
    assert not page(shell, "nerd_player").built
    btn = next(b for b in page(shell, "playback").findChildren(QPushButton)
               if b.objectName() == "SettingsLink" and b.text().startswith(tr("nerd_page")))
    assert btn.isVisibleTo(page(shell, "playback"))      # Nerd on, as stored
    btn.click()
    assert shell.current_key() == "nerd_player" and page(shell, "nerd_player").built
    self.hide()
    return 0


QDialog.exec = search_and_link
w.open_settings()
# From here on Multiview is never built again: its keys never written.
s.remove("mv_cells")
QDialog.exec = link
w.open_settings()

# 3. About's seven taps with Nerd off and its pages not built: they come out,
# and closing Settings keeps Nerd on.
s.setValue("nerd_enabled", "false")
nerd.install(s)
w.show_about = lambda *a, **k: s.setValue("nerd_enabled", "true")


def about(self):
    self.show()
    shell = self.findChild(SettingsShell)
    assert "nerd_net" in shell._off and not page(shell, "nerd").built
    shell.show_page("general")
    row = next(r for c in page(shell, "general").cards for r, _t in c.rows
               if getattr(r, "label", None) is not None and r.label.text() == tr("menu_about"))
    row.link.click()
    assert wait(lambda: not shell._items["nerd_net"].isHidden()), shell._off
    assert not page(shell, "nerd").built
    shell.show_page("nerd")
    assert switch_named(self, tr("nerd_switch")).isChecked()
    self.hide()
    return 0


QDialog.exec = about
w.open_settings()
assert s.value("nerd_enabled") == "true", s.value("nerd_enabled")

# 4. A source's page opened from the source's menu.
QDialog.exec = lambda self: (self.show(), app.processEvents(), self.hide(), 0)[-1]
w.open_source_settings("e")

# Untouched and never built: not written.
assert s.value("mv_cells") is None, s.value("mv_cells")
assert not stray, stray
# Closed: freed a while later.
assert wait(lambda: sip.isdeleted(kept[0]), 5.0), "a closed Settings window stays"
print("LAZY_OK", flush=True)
"""


def test_settings_opens_on_one_page_and_shows_no_window_of_its_own(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True,
                          text=True, env=env, cwd=_REPO_ROOT, timeout=240)
    assert "LAZY_OK" in proc.stdout, (
        f"stdout={proc.stdout[-2000:]!r}\nstderr={proc.stderr[-4000:]!r}")
