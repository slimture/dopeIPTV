"""The sidebar logo's jump to what plays lands on it exactly: a recording's
card picked in Recordings (its list is built from headed rows, which once
never looked for the jump), and a local file's card picked AND on screen in
a folder far longer than the window (a card revealed while the folder was
still being read was scrolled to where it was, then pushed out of sight).
While a cast runs and nothing plays here, the jump goes to what the TV
plays, and the place marks it "On TV"."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import json, os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
rec, lib = sys.argv[1], sys.argv[2]
_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)
import dopeiptv.providers.cast_bridge as cb
cb.ffmpeg_path = lambda: None
from dopeiptv.core._lazy_requests import requests as rq
def offline(*_a, **_k):
    raise OSError("offline in the test")
rq.get = offline
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow
app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
settings.setValue("music_art_online", "false")
settings.setValue("recordings_dir", rec)
settings.setValue("local_dirs", json.dumps([lib]))
mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
w._start_playback = lambda *a, **k: None
w.resize(1440, 900)
w.show()

def settle(cond, secs=20, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what

def playing(kind, group, key, item):
    # What _start_playback leaves behind for the jump (it is stubbed here).
    w._playing_key, w._playing_group, w._playing_item = key, group, None
    w._last_playback = {"url": key, "title": item["name"], "icon_url": None, "key": key,
                        "kind": kind, "item": item, "series_ctx": None}

def card_on_screen(page, key):
    v = page.view
    ix = v.currentIndex()
    items = v.items()
    if not ix.isValid() or ix.row() >= len(items) or items[ix.row()].key != key:
        return False
    r = v.visualRect(ix)
    top = v.mapTo(page, r.topLeft()).y()
    return 0 <= top and top + r.height() <= page.height()

def list_row():
    ix = w.listw.currentIndex()
    it = w.list_model.item_at(ix.row()) if ix.isValid() else None
    return (it or {}).get("_key")

settle(lambda: w.list_model.rowCount() > 0, what="first list")
# -- a recording, from TV and from Mine ----------------------------------------------
assert w._show_place("rec") is True
settle(lambda: any(r.get("_kind") == "recording" for r in w.list_model.items()), what="recordings")
target = next(r for r in w.list_model.items() if "Football" in str(r.get("_key")))
playing("recording", "rec", target["_key"], {"name": "Football", "_key": target["_key"]})
for away in ("tv", "mitt"):
    w._show_place(away)
    settle(lambda: w._place == away, what=away)
    w._jump_to_now_playing()
    settle(lambda: w._place == "rec" and list_row() == target["_key"]
           and card_on_screen(w._rp_page, target["_key"]), what="the recording from " + away)
# -- a local file deep in a long folder -----------------------------------------------
path = os.path.join(lib, "Big", "Clip 517.mkv")
playing("local", "local", path, {"name": "Clip 517.mkv", "_key": path, "_path": path})
w._show_place("tv")
settle(lambda: w._place == "tv", what="tv")
w._jump_to_now_playing()
settle(lambda: w._place == "local" and list_row() == path and card_on_screen(w._lp_page, path),
       secs=30, what="the file on screen")
# -- a cast: the player stopped here, the recording on a TV ------------------------
from types import SimpleNamespace
from dopeiptv.ui import media_cards
w._playing_key = w._playing_group = w._playing_item = None
w._last_playback = {}
w.cast = SimpleNamespace(active=object(), duration=0.0, state="PLAYING/None",
                         position=lambda: 0.0, bridged=lambda: False)
w._cast_device = "Living room TV"
w._cast_ctx = {"sid": None, "key": target["_key"], "kind": "recording", "group": "rec",
               "title": "Football", "item": {"name": "Football", "_key": target["_key"]},
               "row_url": target["_key"]}
now = w._now_playing()
assert now is not None and now.on_tv and now.group == "rec", now
w._show_place("tv")
settle(lambda: w._place == "tv", what="tv")
w._jump_to_now_playing()
settle(lambda: w._place == "rec" and list_row() == target["_key"]
       and card_on_screen(w._rp_page, target["_key"]), what="the cast recording")
w._cast_marks_changed()
assert media_cards.on_tv() and w._rp_page.view.playing_key == str(target["_key"])
# Ended: nothing plays anywhere, and the jump says so.
w._cast_device = None
w._cast_marks_changed()
assert not media_cards.on_tv() and w._now_playing() is None
print("NOW_PLAYING_OK", flush=True)
os._exit(0)
"""


def test_the_jump_lands_on_what_plays(tmp_path):
    pytest.importorskip("PyQt6")
    rec = tmp_path / "Recordings"
    rec.mkdir()
    for name, ch in (("Evening News 2026-11-16 21.00.ts", "SVT1"),
                     ("Football 2026-11-15 18.00.ts", "TV4"), ("Docs.ts", "")):
        (rec / name).write_bytes(b"\0" * 4000)
        if ch:
            (rec / (name + ".info.json")).write_text(json.dumps(
                {"title": "", "description": "", "icon": "", "channel": ch, "start": 1763330400}))
    lib = tmp_path / "Videos"
    (lib / "Big").mkdir(parents=True)
    for i in range(600):
        (lib / "Big" / f"Clip {i:03d}.mkv").write_bytes(b"0")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path / "c"),
               XDG_DATA_HOME=str(tmp_path / "d"), XDG_CACHE_HOME=str(tmp_path / "k"))
    proc = subprocess.run([sys.executable, "-c", _CHILD, str(rec), str(lib)],
                          capture_output=True, text=True, env=env, cwd=_REPO_ROOT, timeout=240)
    assert "NOW_PLAYING_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")
