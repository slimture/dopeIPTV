"""The app's places (core/places.py, ui/mw_places.py, ui/mw_popular.py):
the sidebar is the structure - Mine, TV, Movies, Series, Guide, Search,
then Local files, Radio, Recordings - and there is no Home.

Pure: each place's entry and list mode, where the app opens (where the
person left off - a page over the list it came from - or always Mine), and
Home's settings carried over once and taken away. On the real window, in a
child process: the start on the place left, on Mine, on the Guide over its
list; every entry leads to a place that shows; each place's own page
answering through its hook (and Back to it); a profile switch going to that
person's place; popular right now matched to the source, nothing hidden or
locked among it, nothing while switched off."""
from __future__ import annotations

import os
import subprocess
import sys

from dopeiptv.core import places

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class S(dict):
    """Settings as the places see them."""

    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def remove(self, key):
        self.pop(key, None)

    def allKeys(self):  # noqa: N802 - Qt's name
        return list(self)


# -- pure ----------------------------------------------------------------------------

def test_the_places_their_entries_and_modes():
    assert places.PLACES == ("mitt", "tv", "films", "series", "guide", "search",
                             "local", "radio", "rec")
    assert places.UPPER + places.LOWER == places.PLACES
    assert "home" not in places.PLACES and "home" not in places.NAV_OF.values()
    for place in places.PLACES:
        assert places.PLACE_OF_NAV[places.NAV_OF[place]] == place
    for mode in places.MITT_MODES:
        assert places.place_of_mode(mode) == "mitt" and places.nav_of_mode(mode) == "mitt"
    for place, mode in places.MODE_OF.items():
        assert places.place_of_mode(mode) == place
    assert places.place_of_mode("bogus") == places.DEFAULT_PLACE
    assert set(places.SHOW_HOOKS) == set(places.REFRESH_HOOKS) == {"mitt", "tv", "films", "series"}


def test_where_the_app_opens():
    assert places.start_places(S()) == ("tv", "")              # a first start: TV, as ever
    assert places.start_places(S(last_place="films")) == ("films", "")
    s = S(last_place="guide", last_list_place="series")
    assert places.start_places(s) == ("series", "guide")      # the Guide over its list
    assert places.start_places(S(last_place="search")) == ("tv", "search")
    assert places.start_places(S(last_place="nowhere", last_list_place="rec")) == ("rec", "")
    s = S(start_place="mitt", last_place="films")
    assert places.start_places(s) == ("mitt", "")             # always on Mine
    assert places.start_choice(S(start_place="sideways")) == "last"
    s = S()
    places.note(s, "guide", "radio")
    assert s == {"last_place": "guide", "last_list_place": "radio"}
    places.note(s, "films", "films")
    assert s["last_place"] == "films" and s["last_list_place"] == "films"
    places.note(s, "nope", "x")
    assert s["last_place"] == "films"
    assert places.popular_on(S(), "vod") and places.popular_on(S(), "series")
    assert not places.popular_on(S(popular_series="false"), "series")
    assert not places.popular_on(S(), "live")


def test_homes_settings_carried_over_once_and_taken_away():
    s = S({"home_enabled": "true", "home_start": "true", "home_sh_fav": "false",
           "home_sh_pop_movies": "true", "home_pcache_abc": "{}",
           "profiles/anna/home_sh_pop_movies": "false",
           "profiles/anna/home_sh_resume": "true", "profiles/kim/home_sh_pop_series": "false",
           "theme": "nord"})
    assert places.retire_home(s, "anna")
    assert s["start_place"] == "mitt"                         # Open Home at start: Mine
    assert s["popular_movies"] == "false"                     # Anna's shelf was off
    assert "popular_series" not in s                          # Kim is not the one in use
    assert not [k for k in s if "home_" in k and k != places.RETIRED_KEY]
    assert s["theme"] == "nord"
    s["home_start"] = "true"                                  # an old copy's sync, later
    assert not places.retire_home(s, "anna")                  # once
    # Home switched off: "start on Home" meant nothing.
    s = S({"home_enabled": "false", "home_start": "true"})
    places.retire_home(s)
    assert "start_place" not in s and "home_start" not in s
    # A choice made already stays.
    s = S({"home_start": "true", "start_place": "last"})
    places.retire_home(s)
    assert s["start_place"] == "last"


# -- the real window -----------------------------------------------------------------------

_CHILD = r"""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv import i18n
from dopeiptv.core import places, profiles
from dopeiptv.core.profiles import ProfileSettings
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import theme

i18n.set_language("en")
mw.launch_player = lambda *a, **k: None


class Client(DemoClient):
    def vod_categories(self):
        return [{"category_id": "m", "category_name": "Drama"},
                {"category_id": "x", "category_name": "Hidden"}]

    def vod_streams(self, cat=None):
        rows = [{"stream_id": 1, "name": "Dune (2021)", "stream_type": "movie", "category_id": "m"},
                {"stream_id": 2, "name": "Barbie", "stream_type": "movie", "category_id": "x"},
                {"stream_id": 3, "name": "Oppenheimer", "stream_type": "movie", "category_id": "m"}]
        return [r for r in rows if cat in (None, r["category_id"])]

    def series_categories(self):
        return [{"category_id": "s", "category_name": "Crime"}]


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)
        time.sleep(0.005)


def until(cond, secs=5.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        pump(2)
    return cond()


def window(prefill):
    global w
    path = os.path.join(tempfile.mkdtemp(), "s.ini")
    s = ProfileSettings(path, QSettings.Format.IniFormat)
    profiles.activate("")
    for k, v in prefill.items():
        s.setValue(k, v)
    s.setValue("check_updates", "false")
    theme.apply_theme(s)
    w = mw.MainWindow(Client(), s, PlaylistStore(s))
    w.resize(1300, 820)
    w.show()
    until(lambda: w._place == w._start_base and w.list_model.rowCount() >= 0, 2)
    pump(30)
    return w, s


def lit():
    return [k for k, b in w.nav_btns.items() if b.isChecked()]


# -- opens where the person left off -------------------------------------------------
w, s = window({"last_place": "films", "last_list_place": "films"})
assert w.mode == "vod" and w._place == "films" and lit() == ["vod"], (w.mode, w._place, lit())
assert w._place_header.title.text() == "Movies"
w.close()
w, s = window({"last_place": "guide", "last_list_place": "series"})
assert until(lambda: w._guide_page_showing())
assert w.mode == "series" and w._place == "guide" and w._list_place == "series"
w._leave_guide_page()
pump()
assert w._place == "series" and lit() == ["series"]
w.close()
# ...or always on Mine; Home's "start on Home" carried over to it.
w, s = window({"home_enabled": "true", "home_start": "true", "home_sh_pop_movies": "false",
               "last_place": "radio"})
assert w.mode == "fav" and w._place == "mitt" and lit() == ["mitt"], (w.mode, w._place)
assert s.value("start_place") == "mitt" and s.value("popular_movies") == "false"
assert not [k for k in profiles.raw(s).allKeys() if k.startswith("home_s")]
assert not w.popular_on("vod")
got = []
w.popular_titles("vod", got.append)
assert got == [[]]                                         # switched off: nothing asked
# Each place is noted for the next start (the profile's own).
w._show_place("rec")
pump()
assert s.value("last_place") == "rec" and profiles.is_own("last_place")

# -- every entry leads to a place that shows ---------------------------------------------
for key, b in w.nav_btns.items():
    b.click()
    pump(15)
    place = places.PLACE_OF_NAV[key]
    assert w._place == place and lit() == [key], (key, w._place, lit())
    assert w._mid_stack.currentWidget().isVisible()
    assert w._center_stack.currentIndex() == 0
assert not hasattr(w, "_home_page") and not hasattr(w, "_show_home_page")

# -- a place's own page: its hook, Back to it, its refresh ----------------------------------
from PyQt6.QtWidgets import QLabel
own = QLabel("Movies, the place's own page")
w._mid_stack.addWidget(own)
calls = []
def films_show():
    calls.append("show")
    w._mid_stack.setCurrentWidget(own)
    return True
w._place_films_show = films_show
w._place_films_refresh = lambda: calls.append("refresh")
w.nav_btns["vod"].click()
pump()
assert calls == ["show"] and w._mid_stack.currentWidget() is own and w._place == "films"
assert lit() == ["vod"]
w._shortcut_epg_guide()
pump()
assert w._guide_page_showing() and w._list_place == "films"
w._leave_guide_page()                     # Back: the place's own page again
pump()
assert calls == ["show", "show"] and w._mid_stack.currentWidget() is own
w._refresh_places("test")
assert calls[-1] == "refresh"
# A hook that does not show falls back to the list under the header.
w._place_films_show = lambda: False
w._show_place("tv")
w._show_place("films")
pump()
assert w._place == "films" and w._mid_stack.currentWidget() is w._list_page and w.mode == "vod"
del w._place_films_show, w._place_films_refresh

# -- popular right now: the chart matched, what the lists show -------------------------------
s.setValue("popular_movies", "true")
w._popular_chart = {"t": time.time(), "lang": "en", "vod": [
    {"id": 10, "titles": ["Barbie"], "year": 2023}, {"id": 11, "titles": ["Dune"], "year": 2021},
    {"id": 12, "titles": ["Oppenheimer"], "year": 2023}], "series": []}
import dopeiptv.services.popular as popular
popular.is_fresh = lambda *a, **k: True
popular.usable = lambda *a, **k: True
w.overrides.update("vod", "x", hidden=True)                # Barbie's category hidden
changed = []
w.popular_watch(lambda: changed.append(1))
got = []
w.popular_titles("vod", got.append)
assert until(lambda: bool(got))
assert [r["name"] for r in got[0]] == ["Dune (2021)", "Oppenheimer"], got
w._popular_changed()
assert changed == [1]

w.close()

# -- a profile switch: that person's place ---------------------------------------------------
path = os.path.join(tempfile.mkdtemp(), "p.ini")
s = ProfileSettings(path, QSettings.Format.IniFormat)
reg = profiles.start(s, [], "Me")
kid = reg.add("Lisa", colour="pink")
profiles.pinned(s, kid.id).setValue("last_place", "radio")
profiles.pinned(s, kid.id).setValue("last_list_place", "radio")
s.setValue("check_updates", "false")
theme.apply_theme(s)
w = mw.MainWindow(Client(), s, PlaylistStore(s))
w.show()
pump(30)
assert w._place == "tv"
w._show_place("films")
pump()
assert w.switch_profile(kid.id)
pump(20)
assert w._place == "radio" and w.mode == "radio" and lit() == ["radio"], (w._place, w.mode)
assert profiles.pinned(s, profiles.MAIN_ID).value("last_place") == "films"   # Me's, kept
print("PLACES_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_window_opens_where_left_and_every_place_shows(tmp_path):
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                         env=_child_env(tmp_path), capture_output=True, text=True, timeout=240)
    assert "PLACES_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
