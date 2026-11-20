"""The column under the video while an episode plays: its series' poster and
the episode's name - never the row the list had picked before, a TV channel
as often as not, whose logo stayed there and whose button would have started
that channel (seen on a nightly after 1.6.8). In the real window, offscreen,
in a child process as the other window tests."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
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
channel = {"name": "Kanal Ett", "stream_id": 7, "stream_icon": ""}
w._current_key = w._item_key(channel)
w._show_detail(channel)
assert w._detail_name == "Kanal Ett", w._detail_name
assert w.d_logo.width() == w.POSTER_SIZE_LIVE[0]

w.series_ctx = None
w._detail_follow_episode({"id": 5, "name": "Pilot"}, "ep-5")
assert w._detail_name == "Kanal Ett", "no series: the column as it was"

w.series_ctx = {"name": "Example Show", "cover": ""}
w._detail_follow_episode({"id": 5, "name": "Pilot"}, "ep-5")
assert w._current_key == "ep-5", w._current_key
assert w._detail_name == "Pilot", w._detail_name
assert w.d_logo.width() == w.POSTER_SIZE_MEDIA[0], "a series' poster, not a channel's logo"
assert w.d_logo.text() == "E", w.d_logo.text()
print("ok")
"""


def test_an_episode_shows_its_series_under_the_video():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=_REPO_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip().endswith("ok"), out.stdout + out.stderr


def test_every_episode_start_goes_through_it():
    """_start_playback is the one way in for every episode - the series'
    page, Continue, the next one, TV mode, the phone, watch together."""
    src = (Path(_REPO_ROOT) / "dopeiptv" / "ui" / "main_window.py").read_text(encoding="utf-8")
    body = src.split("def _start_playback(", 1)[1].split("\n    def ", 1)[0]
    assert 'if kind == "episode":\n            try:\n                self._detail_follow_episode(item, key)' in body
