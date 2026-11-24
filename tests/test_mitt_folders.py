"""Mine's Favorite folders across every source, in the real window
(ui/mw_mitt.py, offscreen in a child process): two sources' folders of one
name are one tile, each title says its source and plays from it as Mine's
Overview plays it - another source's channel without switching
(mw_search_all._play_channel_elsewhere), its film once that source is
switched to; a move, a reorder, a title out of its folder or out of the
favorites act on each title's own source's store; a folder renamed,
coloured, locked, opened or removed is so in every source that holds it -
all of them or none (one store that fails puts the others back, said); a
locked folder asks for the PIN whichever source locked it; the parental
lock on categories holds for every source's titles; a kids profile sees
the source in use alone and never a locked folder; thousands of
favourites over several sources keep the window's thread quick (read on
Mine's worker)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import faulthandler, json, os, tempfile, time
faulthandler.dump_traceback_later(240, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QSettings
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv import i18n
from dopeiptv.core import mitt
from dopeiptv.core.stores import CategoryOverrides, FavoriteStore, PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers import epg as epgmod
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.mw_mitt import LOOSE

i18n.set_language("en")
app = QApplication.instance() or QApplication([])
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
epgmod.XmltvGuide.PARSE_ELSEWHERE = False
epgmod.XmltvGuide._download = lambda self: (_ for _ in ()).throw(OSError("offline"))
mw_mod.launch_player = lambda *a, **k: None                  # never a real mpv


class Pirat(DemoClient):
    def vod_url(self, sid, ext=None):
        return f"http://pa.invalid/movie/{sid}.{ext or 'mkv'}"


class Nordic(DemoClient):
    def vod_url(self, sid, ext=None):
        return f"http://pb.invalid/movie/{sid}.{ext or 'mkv'}"


settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
store = PlaylistStore(settings)
store.add({"id": "pa", "name": "Pirat", "kind": "xtream", "server": "http://pa.invalid",
           "username": "u", "password": "p", "epg_url": "", "refresh": "never"})
store.add({"id": "pb", "name": "Nordic", "kind": "xtream", "server": "http://pb.invalid",
           "username": "u", "password": "p", "epg_url": "", "refresh": "never"})
store.set_active("pa")
w = MainWindow(Pirat(), settings, store)
w.cover.resolver = None
w._choice_dialog = lambda *a, **k: 0
toasts = []
_toast = w._show_toast
w._show_toast = lambda text, *a, **k: toasts.append(text)


def settle(cond, secs=15, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what


w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0 or w.mode != "live", what="the window")

plays = []
def fake_play(url, title, start=0.0, **_k):
    plays.append(url)
    w.player.current_url = url
    return True
w.player.play = fake_play

# -- the stores: Pirat (in use) the window's own, Nordic's kept in the settings -----------
own = w._own(w.settings)
chans = {c["stream_id"]: dict(c) for c in w.client.live_streams(None)}   # ids 1..5, "demo"
w.favs.add("all", chans[1])
w.favs.add("Sport", chans[3])
w.favs.add("Sport", chans[4])
w.favs.add("Barn", chans[2])
w.favs.set_group_locked("Barn", True)                        # locked in Pirat alone
w.favs.add("Hemligt", {"stream_id": 50, "name": "Locked category", "category_id": "lk"})
w.overrides.update("live", "lk", locked=True)               # Pirat's own category lock
w.movie_favs.add("Sport", {"stream_id": 40, "name": "Matchen", "category_id": "m1",
                           "container_extension": "mkv"})
own.setValue("favorites_pb", json.dumps({
    "all": [{"stream_id": 76, "name": "Nordic News", "category_id": "n"}],
    "Sport": [{"stream_id": 77, "name": "Nordic Sport", "category_id": "n"},
              {"stream_id": 78, "name": "Nordic Golf", "category_id": "n"},
              {"stream_id": 81, "name": "Nordic Locked", "category_id": "nl"},
              {"stream_id": 82, "name": "Nordic Tennis", "category_id": "n"}],
    "Nordic only": [{"stream_id": 79, "name": "Nordic Kids", "category_id": "n"}],
    "Vuxen": [{"stream_id": 80, "name": "Nordic Late", "category_id": "n"}]}))
own.setValue("favorites_pb_locked", json.dumps(["Vuxen"]))      # locked in Nordic alone
own.setValue("movie_favorites_pb", json.dumps({
    "Sport": [{"stream_id": 900, "name": "Nordic Film", "category_id": "nm",
               "container_extension": "mp4"}]}))
CategoryOverrides(w.settings, "category_overrides_pb").update("live", "nl", locked=True)

LOCK = tr("cat_locked_suffix")


def tiles():
    return [c.title for c in page.folder_tiles.items()]


def tile(name):
    return next(c for c in page.folder_tiles.items()
                if (c.data == name if name != LOOSE else c.key == LOOSE))


def cards():
    return page.folder_items.items()


def card(title):
    return next(c for c in cards() if c.title == title)


def titles():
    return [c.title for c in cards()]


def pb(section="favorites"):
    return json.loads(own.value(f"{section}_pb") or "{}")


def ids(rows):
    return [x["stream_id"] for x in rows]


def act(item):
    page.activated.emit("folder", item)
    app.processEvents()


# -- shown: one folder of a name across both sources --------------------------------------
w._show_place("tv")
assert w._mitt_show() is True
page = w._mitt_page
settle(lambda: w._mitt_data is not None and w._mitt_data.folders is not None, what="Mine read")
# The Overview's favourite channels: every source's, none of a locked
# category (Nordic's nor Pirat's own) nor of a locked folder while shut.
live = {(e.source, e.ident) for e in w._mitt_data.live}
assert ("pb", "77") in live and ("pa", "3") in live, live
assert not live & {("pb", "81"), ("pa", "50"), ("pb", "80"), ("pa", "2")}, live
page.set_tab("folders")
w._mitt_render()
assert tiles()[0] == tr("nav_favorites") and tiles().count("Sport") == 1, tiles()
assert "Nordic only" in tiles() and f"Vuxen  {LOCK}" in tiles() and f"Barn  {LOCK}" in tiles()
assert sorted(titles()) == ["Big Buck Bunny", "Nordic News"], titles()   # every source's loose
w._mitt_folder_picked(tile("Sport"))
assert [(c.title, c.data.source) for c in cards()] == [
    ("Sintel", "pa"), ("Art of Motion", "pa"), ("Nordic Sport", "pb"), ("Nordic Golf", "pb"),
    ("Nordic Tennis", "pb"), ("Matchen", "pa"), ("Nordic Film", "pb")], titles()
# Each title says its source, after its kind; Nordic's locked category's not there.
assert card("Sintel").line == f"{tr('mitt_kind_channel')} · Pirat"
assert card("Nordic Sport").line == f"{tr('mitt_kind_channel')} · Nordic"
assert card("Nordic Film").line == f"{tr('mitt_kind_film')} · Nordic"
assert tile("Sport").line == " · ".join([tr("mitt_n_channels", n=5), tr("mitt_n_films", n=2)])

# -- played as Mine plays them: another source's channel without switching ----------------
act(card("Sintel"))
assert w._last_playback["kind"] == "live" and str(w._last_playback["key"]) == "3"
elsewhere = []
w._play_channel_elsewhere = lambda pl, row: elsewhere.append((pl["id"], row))
act(card("Nordic Sport"))
assert [(pid, row["stream_id"]) for pid, row in elsewhere] == [("pb", 77)], elsewhere
assert not any(k.startswith("_") for k in elsewhere[0][1]), elsewhere   # its own row, plain
assert store.active_id == "pa" and w._mitt_showing()

# -- another source's title's menu: played, out of its own favorites, where it is from ----
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
    if pick:
        want = pick.pop(0)
        next(x for x in acts if x.text() == want).trigger()
QMenu.exec = _exec
w._mitt_folder_item_menu(card("Nordic Golf"), QPoint(0, 0))
seen = dict(menus[-1])
assert seen.get(tr("sa_from_source", source="Nordic")) is False, seen       # said, no choice
assert tr("btn_play") in seen and tr("ctx_remove_from_favorites") in seen
assert tr("ctx_cast_to_chromecast") not in seen                            # not the source in use's
assert "Nordic only" in seen and "Sport" not in seen                       # Move to: every folder

# -- a move: each title in its own source's store ----------------------------------------
pa_movies = json.dumps(w.movie_favs.groups)
w._mitt_folder_drop([card("Nordic Golf"), card("Art of Motion")], tile("Nordic only"))
assert "Nordic Golf" not in titles() and "Art of Motion" not in titles()   # at once
assert ids(pb()["Nordic only"]) == [79, 78] and 78 not in ids(pb()["Sport"])
assert ids(w.favs.groups["Nordic only"]) == [4] and 4 not in ids(w.favs.groups["Sport"])
assert json.dumps(w.movie_favs.groups) == pa_movies and "Nordic only" not in pb("movie_favorites")
settle(lambda: tile("Nordic only").line == tr("mitt_n_channels", n=3), what="the counts")

# -- a reorder: each store keeps its own order, the others untouched ---------------------
pa_sport = json.dumps(w.favs.groups["Sport"])
w._mitt_folder_reorder([card("Nordic Tennis")], 0)
assert ids(pb()["Sport"]) == [82, 81, 77], pb()["Sport"]       # the hidden one keeps its place
assert json.dumps(w.favs.groups["Sport"]) == pa_sport
assert titles()[:3] == ["Sintel", "Nordic Tennis", "Nordic Sport"], titles()

# -- renamed in every source that holds it ------------------------------------------------
QInputDialog.getText = staticmethod(lambda *a, **k: ("Sporten", True))
w.favs.set_group_color("Sport", bgcolor="#2FBF71")
w._mitt_folder_rename("Sport")
assert "Sporten" in w.favs.groups and "Sport" not in w.favs.groups
assert "Sporten" in w.movie_favs.groups and "Sport" not in w.movie_favs.groups
assert "Sporten" in pb() and "Sport" not in pb()
assert "Sporten" in pb("movie_favorites") and "Sport" not in pb("movie_favorites")
assert w.favs.group_color("Sporten").get("bgcolor") == "#2FBF71"   # its colour with it
settle(lambda: "Sporten" in tiles() and "Sport" not in tiles(), what="the tile renamed")
assert w._mitt_folder == "Sporten" and "Nordic Tennis" in titles()

# -- one store fails: none of them changed, and said ---------------------------------------
_rename = FavoriteStore.rename_group
def broken(self, old, new):
    if self.key == "movie_favorites_pb":
        raise OSError("disk full")
    return _rename(self, old, new)
FavoriteStore.rename_group = broken
QInputDialog.getText = staticmethod(lambda *a, **k: ("Idrott", True))
w._mitt_folder_rename("Sporten")
FavoriteStore.rename_group = _rename
assert toasts[-1] == tr("mitt_folder_not_changed"), toasts
for groups in (w.favs.groups, w.movie_favs.groups, pb(), pb("movie_favorites")):
    assert "Sporten" in groups and "Idrott" not in groups, groups
assert w.favs.group_color("Sporten").get("bgcolor") == "#2FBF71"
assert "Idrott" not in json.loads(own.value("favorites_pa_colors") or "{}")
settle(lambda: "Sporten" in tiles() and "Idrott" not in tiles(), what="as it was")

# -- coloured in every source -------------------------------------------------------------
w._mitt_folder_colour("Sporten", "#FF9F43")
assert w.favs.group_color("Sporten")["bgcolor"] == "#FF9F43"
assert w.movie_favs.group_color("Sporten")["bgcolor"] == "#FF9F43"
assert json.loads(own.value("favorites_pb_colors"))["Sporten"]["bgcolor"] == "#FF9F43"
settle(lambda: w._mitt_swatch(tile("Sporten"))[0] == QColor("#FF9F43"), what="the swatch")

# -- locked in every source, the PIN asked to open it -------------------------------------
w.parental.set_pin("4321")
w._mitt_folder_lock("Sporten", True)
assert "Sporten" in w.favs.locked_groups()
assert "Sporten" in json.loads(own.value("favorites_pb_locked"))
assert not w.parental.session_unlocked
settle(lambda: f"Sporten  {LOCK}" in tiles(), what="the lock said on its tile")
assert cards() == [] and page.folder_empty.isVisibleTo(page)           # its titles hidden
w._mitt_folder_picked(tile(LOOSE))
asked = []
w._request_unlock = lambda: asked.append("pin") or False
w._mitt_folder_picked(tile("Sporten"))
assert asked == ["pin"] and w._mitt_folder == LOOSE
# Nordic's own locked folder asks the same; the PIN given, its titles shown.
w._mitt_folder_picked(tile("Vuxen"))
assert asked == ["pin", "pin"] and w._mitt_folder == LOOSE
w._request_unlock = lambda: setattr(w.parental, "session_unlocked", True) or True
w._mitt_folder_picked(tile("Vuxen"))
assert titles() == ["Nordic Late"], titles()
# Opened (the lock and the PIN) in every source.
w._mitt_folder_lock("Sporten", False)
assert "Sporten" not in w.favs.locked_groups()
assert "Sporten" not in json.loads(own.value("favorites_pb_locked"))
w.parental.session_unlocked = False
w._mitt_render()
settle(lambda: w._mitt_data.made_for == w._mitt_made_for() and "Sporten" in tiles(),
       what="read again for the lock shut")
assert w._mitt_folder == LOOSE or w._mitt_folder in ("Vuxen",)

# -- the parental lock on categories, for every source's titles ---------------------------
w._mitt_folder_picked(tile("Sporten"))
assert "Nordic Locked" not in titles()                                     # Nordic's locked category
w._mitt_folder_picked(tile("Hemligt"))
assert titles() == [], titles()                                            # Pirat's own
w.parental.session_unlocked = True
w._mitt_render()
settle(lambda: w._mitt_data.made_for == w._mitt_made_for(), what="read for the lock open")
w._mitt_folder_picked(tile("Sporten"))
settle(lambda: "Nordic Locked" in titles(), what="Nordic's locked category with the lock open")
w.parental.session_unlocked = False
w._mitt_render()
settle(lambda: w._mitt_data.made_for == w._mitt_made_for(), what="the lock shut again")

# -- out of a source's favorites, and a folder removed in every source --------------------
w._mitt_folder_picked(tile("Nordic only"))
pick.append(tr("ctx_remove_from_favorites"))
w._mitt_folder_item_menu(card("Nordic Kids"), QPoint(0, 0))
assert 79 not in [x["stream_id"] for rows in pb().values() for x in rows]
assert "Nordic Kids" not in titles()
w._request_unlock = lambda: True
w._mitt_folder_remove("Nordic only")
assert "Nordic only" not in w.favs.groups and "Nordic only" not in pb()
settle(lambda: "Nordic only" not in tiles(), what="the folder gone")
assert w._mitt_folder == LOOSE

# -- a kids profile: the source in use alone, never a locked folder -----------------------
w._profile_kids = lambda: True
w._mitt_render()
settle(lambda: w._mitt_data is not None and w._mitt_data.made_for[1]
       and w._mitt_data.folders is not None and w._mitt_data.folders.made_for[1],
       what="read for a kids profile")
w._mitt_render()
assert not any(t.startswith("Barn") or t.startswith("Vuxen") for t in tiles()), tiles()
assert "Sporten" in tiles()
for name in [n for n in w._mitt_folder_names()] + [LOOSE]:
    w._mitt_folder_picked(tile(name))
    assert all(c.data.source in ("pa", "") for c in cards()), (name, titles())
assert "Nordic Late" not in titles() and not any(c.title.startswith("Nordic") for c in cards())
del w._profile_kids
w._mitt_render()
settle(lambda: w._mitt_data is not None and not w._mitt_data.made_for[1]
       and any(t.startswith("Vuxen") for t in tiles()), what="a grown-up again")

# -- another source's film: that source switched to, then played ---------------------------
mw_mod.make_client = lambda pl: Nordic() if pl.get("id") == "pb" else Pirat()
w._mitt_folder_picked(tile("Sporten"))
act(card("Nordic Film"))
settle(lambda: store.active_id == "pb" and plays and plays[-1] == "http://pb.invalid/movie/900.mp4",
       what="Nordic's film after the switch")
assert w._last_playback["kind"] == "movie" and str(w._last_playback["key"]) == "900"

# -- big: thousands of favourites over four sources ---------------------------------------
for pid, name in (("pc", "Third"), ("pd", "Fourth")):
    store.add({"id": pid, "name": name, "kind": "xtream", "server": f"http://{pid}.invalid",
               "username": "u", "password": "p", "epg_url": "", "refresh": "never"})
for pid in ("pa", "pc", "pd"):
    own.setValue(f"favorites_{pid}", json.dumps(
        {"all": [{"stream_id": 10_000 + i, "name": f"{pid} channel {i}", "category_id": "big"}
                 for i in range(1500)],
         **{f"Folder {k}": [{"stream_id": 20_000 + k * 10 + j, "name": f"{pid} {k}.{j}",
                             "category_id": "big"} for j in range(10)] for k in range(20)}}))
w.favs.groups["all"] = [{"stream_id": 30_000 + i, "name": f"pb channel {i}", "category_id": "big"}
                        for i in range(1500)]
w._show_place("tv")
t0 = time.perf_counter()
w._show_place("mitt")
gui = time.perf_counter() - t0
settle(lambda: w._mitt_data is not None and w._mitt_data.folders is not None
       and len(w._mitt_data.folders.loose.entries) >= 6000, 30, what="the big folders")
page.set_tab("folders")
w._mitt_folder_picked(tile(LOOSE))
t1 = time.perf_counter()
w._mitt_folders_fill()
fill = time.perf_counter() - t1
assert len(cards()) >= 6000 and tiles().count("Folder 7") == 1
t2 = time.perf_counter()
snap = w._mitt_fav_snapshot()
copy = time.perf_counter() - t2
print(f"MITT_FOLDER_TIMES gui={gui*1000:.0f}ms fill={fill*1000:.0f}ms "
      f"snapshot={copy*1000:.0f}ms", flush=True)
assert gui < 1.0 and fill < 0.8 and copy < 0.2, (gui, fill, copy)
assert any(c.raw for c in snap.stores)                    # another source's parsed on the worker
print("MITT_FOLDERS_OK", flush=True)
os._exit(0)
"""


def test_mines_folders_across_every_source(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path),
               XDG_CACHE_HOME=str(tmp_path / "cache"), XDG_DATA_HOME=str(tmp_path / "data"))
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=300)
    assert "MITT_FOLDERS_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-5000:]!r}")
