"""Mine's menus in the real window (ui/mw_mitt.py): each row's right click
offers what the lists offer, and what it says happens in the stores the
rest of the app reads - History's Remove (this source's and another's), the
queue's Move to the top and Remove from Watch Later, Continue's Remove, a
channel's and a film's favourite heart, a channel's address copied, another
source's row only played or opened (with where it is from), and the
folders' menus: rename, a title out of its folder, a folder removed."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import json, os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import datetime, timedelta
from PyQt6.QtCore import QPoint, QSettings
from PyQt6.QtWidgets import QApplication, QMenu

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.core import mitt
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

# Every menu the window opens: what it offers kept, and the entry the test
# names chosen as a click would choose it.
menus, pick = [], []
def _all(menu):
    out = []
    for a in menu.actions():
        if a.menu() is not None:
            out += _all(a.menu())
        elif not a.isSeparator():
            out.append(a)
    return out
def _exec(self, *a, **k):
    acts = _all(self)
    menus.append([(x.text(), x.isEnabled()) for x in acts])
    if not pick:
        return None
    want = pick.pop(0)
    for x in acts:
        if x.text() == want:
            assert x.isEnabled(), f"{want!r} is offered but off"
            x.trigger()
            return x
    raise AssertionError(f"no {want!r} in {[x.text() for x in acts]}")
QMenu.exec = _exec

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
store = PlaylistStore(settings)
store.add({"id": "pa", "name": "Pirat", "kind": "xtream", "server": "http://pa.invalid",
           "username": "u", "password": "p", "epg_url": "", "refresh": "never"})
store.add({"id": "pb", "name": "Nordic", "kind": "xtream", "server": "http://pb.invalid",
           "username": "u", "password": "p", "epg_url": "", "refresh": "never"})
store.set_active("pa")
mw_mod.launch_player = lambda *a, **k: None                  # never a real mpv
w = MainWindow(DemoClient(), settings, store)
w.cover.resolver = None
w._choice_dialog = lambda *a, **k: 0
w.player.play = lambda *a, **k: True
w.client.vod_url = lambda sid, ext=None: f"http://pa.invalid/movie/{sid}.{ext or 'mkv'}"

def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out"

w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0 or w.mode != "live")

now = datetime.now()
chans = w.client.live_streams(None)
own = w._own(w.settings)
for c in chans[:3]:
    w.favs.add("all", dict(c))
w.favs.add("Barn", dict(chans[3]))
w.movie_favs.add("all", {"stream_id": 40, "name": "Favourite Film"})
own.setValue("history_pb", json.dumps([{"name": "Nordic One", "_kind": "live", "_key": 77,
                                        "_url": "http://pb.invalid/77", "stream_id": 77,
                                        "_watched_at": (now - timedelta(minutes=5)).isoformat()}]))
w.resume.record("vod", 30, 1200, 3600, item={"stream_id": 30, "name": "Northern Lights"})
def h(name, kind, key, ago, **kw):
    return {"name": name, "_kind": kind, "_key": key, "_url": f"http://pa.invalid/{kind}/{key}",
            "_watched_at": (now - ago).isoformat(), **kw}
w.history.entries = [
    h("Northern Lights", "movie", 30, timedelta(minutes=20)),
    h("Stjärnfall", "movie", 31, timedelta(minutes=40)),
]
w.history._save()
for sid, name in ((41, "Queue Film A"), (42, "Queue Film B"), (43, "Queue Film C")):
    w.watchlist.add_movie_local({"stream_id": sid, "name": name}, None)

w._show_place("tv")
assert w._mitt_show() is True
page = w._mitt_page
settle(lambda: w._mitt_data is not None and page.sections["queue"].isVisibleTo(page))

def items(name):
    return page.section_items(name)

def keyed(name, pred):
    for it in items(name):
        e = getattr(it, "data", None)
        if isinstance(e, mitt.Entry) and pred(e):
            return it
    raise AssertionError(f"not in {name}: {[getattr(i, 'title', '') for i in items(name)]}")

def choose(section, item, text):
    pick.append(text)
    w._mitt_menu(section, item, QPoint(0, 0))
    assert not pick, f"{text!r} was not chosen"
    app.processEvents()

def offered():
    return [t for t, on in menus[-1]]

def rebuilt(cond):
    w._mitt_build()
    settle(cond)

# -- History: this source's row out of History ------------------------------------------
choose("history", keyed("history", lambda e: e.ident == "31"), tr("local_missing_remove"))
assert [x["_key"] for x in w.history.entries] == [30], w.history.entries
assert not any(getattr(c.data, "ident", None) == "31" for c in items("history")
               if isinstance(getattr(c, "data", None), mitt.Entry))

# -- another source's row: only played, with where it is from; its own History ---------
nordic = keyed("history", lambda e: e.source == "pb")
pick.append(tr("local_missing_remove"))
w._mitt_menu("history", nordic, QPoint(0, 0))
seen = dict(menus[-1])
assert seen.get(tr("sa_from_source", source="Nordic")) is False, seen      # said, not a choice
assert tr("btn_play") in seen and tr("ctx_add_to_favorites") not in seen
assert json.loads(own.value("history_pb") or "[]") == [], "out of Nordic's History"

# -- the queue: to the top, then off Watch Later ----------------------------------------
choose("queue", keyed("queue", lambda e: e.ident == "43"), tr("mitt_move_top"))
assert [c.title for c in items("queue")][:3] == ["Queue Film C", "Queue Film A", "Queue Film B"]
rebuilt(lambda: [c.title for c in items("queue")][:1] == ["Queue Film C"])   # the order kept
choose("queue", keyed("queue", lambda e: e.ident == "41"), tr("ctx_watchlist_remove"))
assert not w.watchlist.has_movie(None, 41)
rebuilt(lambda: "Queue Film A" not in [c.title for c in items("queue")])
# The queue row's own Remove (its painted ✕, Delete): off Watch Later and off the page.
w._mitt_queue_remove(keyed("queue", lambda e: e.ident == "42"))
assert not w.watchlist.has_movie(None, 42)
assert "Queue Film B" not in [c.title for c in items("queue")]

# -- Continue: the resume point forgotten ------------------------------------------------
assert w.resume.saved_position(30, "movie") == 1200
choose("continue", keyed("continue", lambda e: e.kind == "film"), tr("ctx_continue_remove"))
assert w.resume.saved_position(30, "movie") == 0.0
rebuilt(lambda: not any(getattr(c.data, "ident", None) == "30" for c in items("continue")))

# -- a favourite channel: its address copied, then out of the favorites ------------------
QApplication.clipboard().setText("")
first = keyed("live", lambda e: e.kind == "channel" and e.ident == str(chans[0]["stream_id"]))
choose("live", first, tr("ctx_copy_stream_url"))
copied = QApplication.clipboard().text()
assert copied and "://" in copied, copied
second = keyed("live", lambda e: e.kind == "channel" and e.ident == str(chans[1]["stream_id"]))
choose("live", second, tr("ctx_remove_from_favorites"))
assert not w.favs.is_favorite(chans[1]["stream_id"])
assert w.favs.is_favorite(chans[0]["stream_id"]), "only that one"
for want in (tr("common_watch"), tr("btn_epg_guide"), tr("ctx_cast_to_chromecast")):
    assert want in offered(), (want, offered())

# -- a favourite film: the heart off -----------------------------------------------------
rebuilt(lambda: any(getattr(c.data, "ident", None) == "40" for c in items("favorites")))
choose("favorites", keyed("favorites", lambda e: e.ident == "40"), tr("ctx_remove_from_favorites"))
assert not w.movie_favs.is_favorite(40)

# -- the folders: renamed, a title out of it, one removed --------------------------------
page.set_tab("folders")
w._mitt_render()
barn = next(c for c in page.folder_tiles.items() if c.title == "Barn")
from dopeiptv.ui import message_box
message_box.get_text = lambda *a, **k: ("Kids", True)
pick.append(tr("ctx_rename_folder"))
w._mitt_folder_menu(barn, QPoint(0, 0))
assert "Kids" in w.favs.groups and "Barn" not in w.favs.groups
assert any(t == tr("ctx_lock_group") for t in offered()), "a folder can be locked"

# The folders are read again on Mine's worker: the tiles follow a moment later.
settle(lambda: any(c.title == "Kids" for c in page.folder_tiles.items()))
kids = next(c for c in page.folder_tiles.items() if c.title == "Kids")
w._mitt_folder_picked(kids)
[card] = page.folder_items.items()
pick.append(tr("mitt_out_of_folder"))
w._mitt_folder_item_menu(card, QPoint(0, 0))
assert not w.favs.groups.get("Kids"), w.favs.groups.get("Kids")
assert not page.folder_items.items(), "out of the folder shown at once"

# A favourite filed elsewhere (the sidebar's menus): the tab reads it again.
w.favs.add("Tmp", dict(chans[2]))
w._mitt_render()
settle(lambda: any(c.title == "Tmp" for c in page.folder_tiles.items()))
tmp = next(c for c in page.folder_tiles.items() if c.title == "Tmp")
pick.append(tr("ctx_remove_folder", group="Tmp"))
w._mitt_folder_menu(tmp, QPoint(0, 0))
assert "Tmp" not in w.favs.groups
assert w.favs.is_favorite(chans[2]["stream_id"]), "still a favorite outside the folder"
assert pick == []
print("MITT_MENUS_OK", flush=True)
os._exit(0)
"""


def test_mines_menus_do_what_they_say(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=300)
    assert "MITT_MENUS_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-5000:]!r}")
