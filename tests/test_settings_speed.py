"""Settings opens without the work it did not need, and the video playing
beside it keeps going (the GUI thread draws it).

- A page that is not on show is not laid out: neither the stack of pages
  asking every page for its size as the window shows, nor a row shown or
  hidden by a search or a shown_if, lays out a row there.
- The pages' sidebar icons are drawn once a session, not every opening.
- In the real window (a child process, as test_settings_window): nothing
  walks a directory or touches the network on the window's thread while
  Settings opens, with Nerd on and several kinds of source; the image cache
  is counted off it once the window is up and its row says the size; and
  Nerd's checks start only once the window is shown.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget  # noqa: E402

from dopeiptv.ui import settings_ui  # noqa: E402
from dopeiptv.ui.settings_ui import SettingsPage, SettingsShell, page_icon  # noqa: E402

app = QApplication.instance() or QApplication([])
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Probe(QWidget):
    """Counts how often it is asked for its size: it is, when its page is
    laid out."""

    def __init__(self) -> None:
        super().__init__()
        self.asked = 0

    def sizeHint(self):                                  # noqa: N802
        self.asked += 1
        return super().sizeHint()


def _blank(_kind, size, _colour):
    return QPixmap(size, size)


def test_a_page_not_on_show_is_not_laid_out():
    d = QDialog()
    QVBoxLayout(d).addWidget(shell := SettingsShell("Search", "none"))
    first = shell.add_page(SettingsPage("first", "First", "On show"),
                           page_icon("gear", _blank))
    first.card("A").row("A row")
    other = shell.add_page(SettingsPage("other", "Other", "Not on show"),
                           page_icon("gear", _blank))
    card = other.card("B")
    probe = _Probe()
    card.widget(probe, "probe")
    switch = card.toggle("Switch", True)
    gated = card.row("Gated")
    card.shown_if(gated, switch.isChecked)
    for _ in range(3):                     # rows shown again and again
        card.refresh()
    shell.arrange([("Group", ["first", "other"])])
    shell.show_page("first")
    d.resize(900, 600)
    d.show()
    for _ in range(5):
        app.processEvents()
    assert probe.asked == 0, probe.asked
    switch.setChecked(False)
    card.refresh()
    switch.setChecked(True)
    card.refresh()
    assert probe.asked == 0, probe.asked
    # Shown, it is laid out as before; the rows are where they were.
    shell.show_page("other")
    for _ in range(5):
        app.processEvents()
    assert probe.asked > 0
    assert probe.isVisible() and gated.isVisible()
    # A page asks for what QScrollArea gives any page longer than a few rows.
    h = other.fontMetrics().height()
    assert other.sizeHint() == QSize(36 * h, 24 * h)
    d.close()


def test_page_icons_are_drawn_once(monkeypatch):
    drawn = []
    real = settings_ui._draw_own_icon

    def draw(kind, s, colour):
        drawn.append((kind, s, colour))
        return real(kind, s, colour)
    monkeypatch.setattr(settings_ui, "_draw_own_icon", draw)
    monkeypatch.setattr(settings_ui, "_OWN_ICONS", {})
    for _ in range(3):
        icon = page_icon("palette", _blank, tint="#123456")
        assert not icon.isNull()
    # Its two colours (at rest, picked), each drawn once.
    assert len(drawn) == 2, drawn
    page_icon("palette", _blank, tint="#654321")
    assert len(drawn) == 3


_CHILD = r"""
import json, os, socket, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
tmp = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = os.path.join(tmp, "cache")
from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("nerd_enabled", "true")
s.setValue("playlists", json.dumps([
    {"id": "x", "name": "Xtream", "kind": "xtream", "server": "http://x.invalid",
     "username": "u", "password": "p"},
    {"id": "m", "name": "List", "kind": "m3u", "server": "http://m.invalid/l.m3u"},
    {"id": "s", "name": "Portal", "kind": "stalker", "server": "http://s.invalid/c/",
     "mac": "00:1A:79:00:00:01"},
    {"id": "e", "name": "Box", "kind": "enigma2", "server": "http://192.0.2.10"},
    {"id": "j", "name": "Jelly", "kind": "jellyfin", "server": "http://192.0.2.20:8096",
     "token": "t", "user_id": "u", "server_id": "s"}]))
s.setValue("active_playlist", "x")
from dopeiptv.core import nerd
nerd.install(s)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.core.workers import default_image_cache_dir
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import nerd_check_line
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell

w = MainWindow(DemoClient(), s, PlaylistStore(s))
images = default_image_cache_dir("images")
images.mkdir(parents=True, exist_ok=True)
for i in range(40):
    (images / f"{i}.png").write_bytes(b"x" * 500)

main = threading.main_thread()
guard = {"on": False}
caught = []


def watch(mod, name):
    real = getattr(mod, name)

    def f(*a, **k):
        if guard["on"] and threading.current_thread() is main:
            caught.append(name)
            raise AssertionError(name + " on the window's thread while Settings opens")
        return real(*a, **k)
    setattr(mod, name, f)


for name in ("scandir", "listdir", "walk"):
    watch(os, name)
for name in ("getaddrinfo", "create_connection"):
    watch(socket, name)
watch(socket.socket, "connect")
import requests
watch(requests.Session, "request")

started = []
real_start = nerd_check_line.CheckLine.start


def start(self):
    started.append(self.window().isVisible())
    return real_start(self)


nerd_check_line.CheckLine.start = start
state = {}


def run(self):
    guard["on"] = False                  # built: the rest is the event loop's
    state["built_index"] = list(w._settings_index)
    self.show()
    end = time.monotonic() + 8
    label = tr("settings_image_cache_label", size="…")
    shell = self.findChild(SettingsShell)
    adv = next(p for p in shell.pages if p.key == "advanced")
    row = next(r for c in adv.cards for r, _t in c.rows
               if getattr(r, "label", None) is not None
               and r.label.text().startswith(label[:12]))
    while time.monotonic() < end and (row.label.text() == label or not started):
        app.processEvents()
        time.sleep(0.01)
    state["label"] = row.label.text()
    self.hide()
    return 0


QDialog.exec = run
w.open_settings()                        # the first: its imports made
caught.clear(); started.clear()
guard["on"] = True
w.open_settings()
guard["on"] = False
assert not caught, caught
assert state["label"] != tr("settings_image_cache_label", size="…"), state
from dopeiptv.core.recording import format_size
assert state["label"] == tr("settings_image_cache_label", size=format_size(40 * 500)), state
assert started and all(started), started
# The palette's index is the window's as before: every row, Nerd's too.
assert any(r["nerd"] for r in state["built_index"])
print("SPEED_OK", flush=True)
"""


def test_settings_opens_without_disk_walks_or_network_on_the_window_thread(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True,
                          text=True, env=env, cwd=_REPO_ROOT, timeout=180)
    assert "SPEED_OK" in proc.stdout, (
        f"stdout={proc.stdout[-2000:]!r}\nstderr={proc.stderr[-4000:]!r}")
