"""A kids profile's "Only what a grown-up allows" (core/profiles.py
``only_allowed``, off by default): only the categories a grown-up chose,
per source, are shown or played anywhere - the lists, the category column,
the favourites, the guide, the phone remote, the remote API and Home
Assistant, multiview, the workers' catalogue (the palette, TV mode), the
search across sources, the places (no Radio) - and nothing else plays,
whichever way it is asked. A kids profile without it, and a grown-up, as
before. The real window in a child process (offscreen)."""
from __future__ import annotations

import os
import subprocess
import sys

from dopeiptv.core import profiles
from dopeiptv.core.profiles import Profile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_mode_is_off_unless_chosen_and_kept_clean():
    old = Profile.from_dict({"id": "k", "name": "Kid", "kids": True})
    assert not old.only_allowed and not old.allow_only and old.allowed == {}
    assert old.allowed_for("p1", "live") is None            # existing kids profiles unchanged
    p = Profile.from_dict({"id": "k", "name": "Kid", "kids": True, "only_allowed": True,
                           "allowed": {"p1": {"live": ["2", 3, True, None], "vod": [],
                                              "other": ["x"]}, "": {"live": ["1"]},
                                       "p2": "nonsense"}})
    assert p.allow_only and p.allowed == {"p1": {"live": ["2", "3"]}}
    assert p.allowed_for("p1", "live") == {"2", "3"}
    assert p.allowed_for("p1", "vod") == frozenset()          # nothing chosen: nothing
    assert p.allowed_for("p9", "live") == frozenset()         # another source: nothing
    # Only for a kids profile.
    grown = Profile.from_dict({"id": "g", "name": "Me", "only_allowed": True,
                               "allowed": {"p1": {"live": ["1"]}}})
    assert not grown.allow_only and grown.allowed_for("p1", "live") is None
    assert len(profiles.clean_allowed({"p": {"live": [str(i) for i in range(5000)]}})["p"]["live"]) \
        == profiles.ALLOW_MAX


def test_it_is_kept_with_the_profile_and_never_in_what_others_may_know(tmp_path):
    from PyQt6.QtCore import QSettings
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store = profiles.ProfileStore(s)
    kid = store.add("Kid", kids=True)
    store.update(kid.id, only_allowed=True, allowed={"p1": {"live": ["2"]}})
    again = profiles.ProfileStore(s)
    got = again.get(kid.id)
    assert got.allow_only and got.allowed_for("p1", "live") == {"2"}
    assert "allowed" not in got.public() and "only_allowed" not in got.public()
    # Sync's merge keeps each computer's own.
    again.merge_public([dict(got.public(), name="Kiddo")])
    assert again.get(kid.id).allow_only and again.get(kid.id).name == "Kiddo"


_CHILD = r"""
import faulthandler, json, os, tempfile, threading, time
faulthandler.dump_traceback_later(200, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from dopeiptv.ui import message_box
message_box.warning = lambda *a, **k: 0
message_box.information = lambda *a, **k: 0
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)

from dopeiptv import i18n
from dopeiptv.core import profiles
from dopeiptv.core.profiles import MAIN_ID, ProfileSettings
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw, theme

i18n.set_language("en")
mw.launch_player = lambda *a, **k: None

ROWS = [{"stream_id": 1, "num": 1, "name": "News One", "stream_icon": "", "category_id": "1",
         "epg_channel_id": ""},
        {"stream_id": 2, "num": 2, "name": "Toons", "stream_icon": "", "category_id": "2",
         "epg_channel_id": ""},
        {"stream_id": 3, "num": 3, "name": "Blue Room", "stream_icon": "", "category_id": "3",
         "epg_channel_id": ""},
        {"stream_id": 4, "num": 4, "name": "Late Toons", "stream_icon": "", "category_id": "2",
         "epg_channel_id": "", "is_adult": "1"}]


class Client(DemoClient):
    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"},
                {"category_id": "2", "category_name": "Cartoons"},
                {"category_id": "3", "category_name": "XXX Adult"}]

    def live_streams(self, category_id=None):
        return [dict(r) for r in ROWS if category_id in (None, r["category_id"])]


s = ProfileSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "p1", "name": "Home", "kind": "m3u",
                                     "server": "http://lists.invalid/l.m3u"}]))
s.setValue("active_playlist", "p1")
reg = profiles.start(s, [], "Me")
kid = reg.add("Lisa", colour="pink", kids=True)
reg.update(kid.id, only_allowed=True, allowed={"p1": {"live": ["2"]}})
plain_kid = reg.add("Tom", colour="green", kids=True)
theme.apply_theme(s)
w = mw.MainWindow(Client(), s, PlaylistStore(s))
w.resize(1280, 800)
w.show()


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)


def until(cond, secs=5.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        pump(2)
    return cond()


def ids(rows):
    return [r.get("stream_id") for r in rows]


def on_server(fn):
    out = {}
    t = threading.Thread(target=lambda: out.update(r=fn()))
    t.start()
    while t.is_alive():
        app.processEvents()
        t.join(0.01)
    return out.get("r")


rows = Client().live_streams()
pump(30)
assert w.playlist_store.active_id == "p1", w.playlist_store.active_id
assert ids(w._chno_rows(rows)) == [1, 2, 3, 4]

# -- the kids profile that sees only what a grown-up allows -------------------------
assert w.switch_profile(kid.id)
assert w._kids_only_allowed() and w._kids_allowed("live") == {"2"}
for r in rows:
    w.favs.add("all", dict(r))
w.switch_mode("live")
assert until(lambda: w.cat_list.count() >= 1), "the categories as the list reads them"
pump(20)
names = [w.cat_list.item(i).text().strip() for i in range(w.cat_list.count())]
assert not any(n in ("News", "XXX Adult") for n in names) and "Cartoons" in names, names
assert ids(w._chno_rows(rows)) == [2], ids(w._chno_rows(rows))
assert ids(w._favorite_channels()) == [2]
listed = w._phone_channels(rows)
assert [c["id"] for c in listed["channels"]] == [2], listed
assert ids(w._remote_do("_shown", {"rows": rows})) == [2]
w.all_items = [dict(r) for r in rows]
w._apply_filter()
shown = [w.list_model.item_at(i).get("stream_id") for i in range(w.list_model.rowCount())]
assert shown == [2], shown
assert ids(w._guide_scope(False)[2]) == [2]
w._kids_known().clear()
keep, got = w._catalog_filter("live"), {}
t = threading.Thread(target=lambda: got.update(rows=[r for r in rows if keep(r)]))
t.start()
t.join()
assert ids(got["rows"]) == [2], got
from dopeiptv.ui.mw_remote import _Bridge
w._remote_bridge, w._phone_bridge = _Bridge(w._remote_do), _Bridge(w._phone_do)
w._kids_known().clear()
got = on_server(lambda: w._remote_call("channels", {}))
assert [c["id"] for c in got["channels"]] == [2], got
w._events_favorites()
assert [r["stream_id"] for r in w._events_favs.values()] == [2], w._events_favs
added = []
w.add_to_multiview = lambda *a, **k: added.append(k.get("item", {}).get("stream_id"))
for r in rows:
    w._add_channel_to_multiview(r, 0)
assert added == [2], added
w._pal_catalogue = None
w._palette_load_catalogue()
assert until(lambda: getattr(w, "_pal_catalogue", None) is not None)
assert ids(w._pal_catalogue.chans) == [2]
# Nothing else plays, whichever way it is asked: the one door.
assert w._kids_may_play("live", rows[1]) is True
for bad in (rows[0], rows[2], rows[3]):
    assert w._kids_may_play("live", bad) is False, bad
assert w._kids_may_play("radio", {"name": "Kids FM", "tags": "children"}) is False
# No Radio among the places, on the phone either.
assert "radio" in w._kids_never_places() and "radio" not in w._phone_sections()
w._apply_nav_visibility()
radio_btn = w.nav_btns.get("radio")
assert radio_btn is None or not radio_btn.isVisibleTo(w)
w._show_place("radio")
assert getattr(w, "_place", "") != "radio"
# The search across sources: this source's own allowed set.
plans = w._sa_plans()
mine = next(p for p in plans if p.source.id == "p1")
assert mine.kids and mine.allowed.get("live") == {"2"}, mine.allowed
from dopeiptv.services import search_sources as ss
cat = ss.build_catalogue(mine, Client())
assert ids(cat.channels) == [2], ids(cat.channels)

# -- what a grown-up allows changed while it is in use: shown again at once ---------------
w.profiles.update(kid.id, allowed={"p1": {"live": ["1", "2"]}})
w._profiles_changed()
pump(20)
assert ids(w._chno_rows(rows)) == [1, 2]

# -- a kids profile without it: as before --------------------------------------------------
assert w.switch_profile(plain_kid.id)
assert not w._kids_only_allowed() and w._kids_allowed("live") is None
assert ids(w._chno_rows(rows)) == [1, 2]
assert w._kids_may_play("radio", {"name": "Kids FM", "tags": "children"}) is True
assert "radio" not in w._kids_never_places() and "radio" in w._phone_sections()
# -- a grown-up: everything --------------------------------------------------------------------
assert w.switch_profile(MAIN_ID)
assert ids(w._chno_rows(rows)) == [1, 2, 3, 4] and w._kids_never_places() == set()
print("ALLOWED_OK", flush=True)
os._exit(0)
"""


def test_only_what_a_grown_up_allows_holds_at_every_way_in():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    text = out.stdout + "\n" + out.stderr[-5000:]
    assert "ALLOWED_OK" in out.stdout, text


def test_the_picker_offers_no_category_for_adults_and_keeps_what_is_ticked(tmp_path):
    import json

    from PyQt6.QtCore import QSettings, Qt, QThreadPool
    from PyQt6.QtWidgets import QApplication, QWidget

    from dopeiptv.core.stores import PlaylistStore
    from dopeiptv.ui.profiles_ui import AllowedPicker
    app = QApplication.instance() or QApplication([])

    class C:
        def live_categories(self):
            return [{"category_id": "1", "category_name": "News"},
                    {"category_id": "3", "category_name": "XXX Adult"}]

        def vod_categories(self):
            return [{"category_id": "9", "category_name": "Family films"}]

        def series_categories(self):
            raise OSError("no series here")

    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("playlists", json.dumps([{"id": "p1", "name": "Home", "kind": "m3u",
                                         "server": "http://lists.invalid/l.m3u"}]))
    s.setValue("active_playlist", "p1")
    win = QWidget()
    win.settings, win.client, win.pool = s, C(), QThreadPool()
    win.playlist_store = PlaylistStore(s)
    d = AllowedPicker(None, win, {"p1": {"vod": ["9"]}, "p2": {"live": ["5"]}})
    win.pool.waitForDone(3000)
    for _ in range(10):
        app.processEvents()
    items = [d.list.item(i) for i in range(d.list.count())]
    offered = [it.text() for it in items if it.data(Qt.ItemDataRole.UserRole)]
    assert offered == ["News", "Family films"], offered
    film = next(it for it in items if it.text() == "Family films")
    news = next(it for it in items if it.text() == "News")
    assert film.checkState() == Qt.CheckState.Checked
    assert news.checkState() == Qt.CheckState.Unchecked
    news.setCheckState(Qt.CheckState.Checked)
    film.setCheckState(Qt.CheckState.Unchecked)
    assert d.chosen() == {"p1": {"live": ["1"]}, "p2": {"live": ["5"]}}
    d.deleteLater()
