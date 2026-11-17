"""The Radio place (ui/radio_place.py, ui/mw_place_radio.py).

Without a window: a country's bundled flag, a station's line (what it says
is on while it plays). In the window, with a made-up directory: the place
where the list was (the list its source of truth), its header (the list's
name, how many stations or countries, the filter, Grid / List remembered
for stations), My stations over a list of stations - not over My stations
itself, nor while typing -, a favourite's star, a station played through
the window's path and its menu the list's own, what the station playing
says is on, a country opened and Back (the mouse's back button too), and
nothing asked of the directory but what the list asks."""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_flags_and_lines():
    from dopeiptv.ui.radio_place import flag_path, station_line
    assert flag_path("SE").endswith(os.path.join("flags", "se.svg"))
    assert os.path.isfile(flag_path("se"))
    assert flag_path("qq") == "" and flag_path("") == "" and flag_path("../se") == ""
    assert station_line("Sweden · pop") == "Sweden · pop"
    assert station_line("Sweden · pop", "Song – Artist") == "♪ Song – Artist"


def test_a_line_keeps_the_windows_direction():
    from dopeiptv.ui.list_place import line_of
    assert line_of(["SVT1", "", "1.2 GB"], rtl=False) == "SVT1 · 1.2 GB"
    assert line_of(["SVT1", "1.2 GB"], rtl=True) == "‏SVT1 · 1.2 GB"
    assert line_of([], rtl=True) == ""


_CHILD = r"""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)
from dopeiptv.core._lazy_requests import requests as rq
def offline(*_a, **_k):
    raise OSError("offline in the test")
rq.get = offline
from dopeiptv.core.stores import FAV_DEFAULT_GROUP
from dopeiptv.i18n import tr
from dopeiptv.providers import radio_browser as rb
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod, mw_place_radio
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.media_cards import MARK_FAV
from dopeiptv.ui.mw_radio import FAVS, fav_snapshot

def st(uuid, name, code):
    return {"stationuuid": uuid, "name": name, "url": "http://radio.invalid/" + uuid,
            "stream_icon": "", "homepage": "", "country": "Sweden", "countrycode": code,
            "language": "", "tags": ["pop"], "codec": "MP3", "bitrate": 128, "hls": False,
            "votes": 1, "_kind": "radio", "_key": "radio:" + uuid}
STATIONS = [st("a", "Alpha FM", "SE"), st("b", "Beta Radio", "SE"), st("c", "Gamma", "DE")]
asked = []

class FakeRadio:
    def top(self, which=rb.TOP_VOTED, limit=0):
        asked.append(("top", which))
        return list(STATIONS)
    def lists(self, kind):
        asked.append(("lists", kind))
        return rb.groups(kind, [{"name": "Sweden", "iso_3166_1": "SE", "stationcount": 3},
                                {"name": "Germany", "iso_3166_1": "DE", "stationcount": 9}])
    def group(self, kind, value, limit=0):
        asked.append(("group", value))
        return [s for s in STATIONS if s["countrycode"] == value]
    def search(self, name="", **_k):
        asked.append(("search", name))
        return []
    def click(self, uuid):
        asked.append(("click", uuid))

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
w._radio_client = FakeRadio()
w.radio_favs.add(FAV_DEFAULT_GROUP, fav_snapshot(STATIONS[1]))
played = []
w._start_playback = lambda url, title, icon, key, kind, **k: played.append((url, key, kind))
menus = []
w._radio_context_menu = lambda pos, it, at=None: menus.append((it.get("_key"), at))
w.resize(1440, 900)
w.show()

def settle(cond, secs=20, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what

def pick(data):
    for i in range(w.cat_list.count()):
        if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == data:
            w.cat_list.setCurrentRow(i)
            return
    raise AssertionError(data)

settle(lambda: w.list_model.rowCount() > 0, what="first list")
assert w._show_place("radio") is True
settle(lambda: w._place_radio_showing(), what="the place")
place = w._rap_page
view, h = place.view, place.header
assert not w.listw.isVisible() and w.nav_btns["radio"].isChecked()

# -- a list of stations ---------------------------------------------------------------
pick(rb.TOP_VOTED)
settle(lambda: view.model_.rowCount() == 3 and h.title.text() == tr("radio_cat_top_voted"),
       what="most voted")
assert h.count.text() == tr("count_stations", n=3), h.count.text()
assert [c.key for c in view.items()] == [r["_key"] for r in w.list_model.items()]
assert place.layout_switch.isVisible() and not place._back_row.isVisible()
assert place.favs.isVisible() and [c.key for c in place.favs.cards()] == ["radio:b"]
beta = next(c for c in view.items() if c.key == "radio:b")
alpha = next(c for c in view.items() if c.key == "radio:a")
view.dress(beta)
view.dress(alpha)
assert beta.marks == (MARK_FAV,) and alpha.marks == ()
assert "MP3" in alpha.line and alpha.fit is True

# Played through the window's path; its menu the list's own, at the card.
view.activated.emit(alpha)
assert played[-1] == ("http://radio.invalid/a", "radio:a", "radio"), played
place.favs.activated.emit(place.favs.cards()[0])
assert played[-1][1] == "radio:b"
view.setCurrentIndex(view.model_.index(view.row_of(alpha.key), 0))   # as a right click does
view.menu.emit(alpha, QPoint(4, 4))
assert menus[-1] == ("radio:a", QPoint(4, 4))
cur = w.list_model.item_at(w.listw.currentIndex().row())
assert cur is not None and cur["_key"] == "radio:a"        # the list follows the card
assert not any(a[0] == "click" for a in asked)              # clicks only when turned on

# What the station playing says is on.
w._playing_group, w._playing_key = "radio", "radio:a"
w._radio_icy_now = "Song – Artist"
w._place_radio_playing()                    # as _start_playback says
w._place_radio_icy()
view.dress(alpha)
assert alpha.line == "♪ Song – Artist" and view.playing_key == "radio:a"

# Grid / List remembered.
place.layout_switch.buttons[0].click()
assert view.layout_kind == "wide" and settings.value(mw_place_radio.VIEW_KEY) == "wide"

# -- countries, one opened, and Back --------------------------------------------------
pick(rb.COUNTRY)
settle(lambda: h.title.text() == tr("radio_cat_countries") and view.model_.rowCount() == 2,
       what="countries")
assert h.count.text() == tr("radio_n_countries", n=2)
assert not place.layout_switch.isVisible() and view.layout_kind == "list"
assert not place.favs.isVisible()
sweden = next(c for c in view.items() if c.title == "Sweden")
view.dress(sweden)
assert sweden.image.endswith("se.svg") and sweden.line == tr("count_stations", n=3)
view.activated.emit(sweden)
settle(lambda: h.title.text() == "Sweden" and view.model_.rowCount() == 2, what="Sweden")
assert place._back_row.isVisible()
assert place.back.text() == tr("fp_back_to", name=tr("radio_cat_countries"))
vp = view.viewport()
pos = QPointF(10, 10)
ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos, vp.mapToGlobal(pos),
                 Qt.MouseButton.BackButton, Qt.MouseButton.BackButton,
                 Qt.KeyboardModifier.NoModifier)
QApplication.sendEvent(vp, ev)
settle(lambda: h.title.text() == tr("radio_cat_countries") and view.model_.rowCount() == 2,
       what="back to the countries")
assert not place._back_row.isVisible()

# -- My stations: no row of them over themselves; the filter is the list's ------------------
pick(FAVS)
settle(lambda: h.title.text() == tr("radio_cat_favorites") and view.model_.rowCount() == 1,
       what="my stations")
assert not place.favs.isVisible()
place.filter.setText("zzz")
settle(lambda: view.model_.rowCount() == 0, what="filtered")
assert w.search.text() == "zzz" and place.note.text() == tr("tmdb_no_results")
place.filter.clear()
settle(lambda: view.model_.rowCount() == 1, what="unfiltered")
# Nothing asked of the directory but what the list asked: the top list and
# the countries (once a session), Sweden's stations - each once.
assert sorted(set(asked)) == sorted(asked), asked
assert {a[0] for a in asked} == {"top", "lists", "group"}, asked
w.switch_mode("live")
settle(lambda: not w._place_radio_active() and w.listw.isVisible(), what="TV's list")

# The jump to what plays, from elsewhere: Radio, the country it was played
# from opened again, the station picked there.
w.switch_mode("radio")
pick(rb.COUNTRY)
settle(lambda: view.model_.rowCount() == 2, what="countries again")
view.activated.emit(next(c for c in view.items() if c.title == "Sweden"))
settle(lambda: h.title.text() == "Sweden" and view.model_.rowCount() == 2, what="Sweden again")
station = view.items()[1]
view.activated.emit(station)
key = played[-1][1]
w._playing_group, w._playing_key = "radio", key
w._last_playback = {"kind": "radio", "key": key, "item": dict(station.data)}
w.switch_mode("live")
settle(lambda: not w._place_radio_active(), what="TV again")
w._jump_to_now_playing()
settle(lambda: w._place_radio_active() and h.title.text() == "Sweden"
       and w.listw.currentIndex().isValid()
       and w.list_model.item_at(w.listw.currentIndex().row()).get("_key") == key,
       what="the jump to the station playing")
print("RADIO_PLACE_OK", flush=True)
os._exit(0)
"""


def test_the_radio_place_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path / "c"),
               XDG_DATA_HOME=str(tmp_path / "d"), XDG_CACHE_HOME=str(tmp_path / "k"))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=240)
    assert "RADIO_PLACE_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")
