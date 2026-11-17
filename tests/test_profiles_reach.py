"""A profile's limits wherever the app lists or plays something, on the real
window in a child process (offscreen):

- a kids profile sees and plays nothing the provider marks for adults nor
  anything from a category named for them - not only in the main list but
  in "All", the favourites, the guide, the channels the
  phone remote, the remote API and Home Assistant list and play, multiview
  and a worker's catalogue filter (the palette, programme search);
- while "Who's watching?" waits for a profile's PIN at start, the phone
  remote, the remote API, Home Assistant, a command from outside, the
  window's shortcuts, number keys and the media keys play nothing and list
  nothing of that profile's - until the PIN is given."""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SETUP = r"""
import faulthandler, os, tempfile, threading, time
faulthandler.dump_traceback_later(200, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
QMessageBox.information = staticmethod(lambda *a, **k: 0)
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)

from dopeiptv import i18n
from dopeiptv.core import profiles
from dopeiptv.core.profiles import MAIN_ID, ProfileSettings
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw, theme

i18n.set_language("en")
mw.launch_player = lambda *a, **k: None

ROWS = [{"stream_id": 1, "num": 1, "name": "News One", "stream_icon": "", "category_id": "1",
         "epg_channel_id": ""},
        {"stream_id": 2, "num": 2, "name": "News Late", "stream_icon": "", "category_id": "1",
         "epg_channel_id": "", "is_adult": "1"},
        {"stream_id": 3, "num": 3, "name": "Blue Room", "stream_icon": "", "category_id": "3",
         "epg_channel_id": ""}]


class Client(DemoClient):
    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"},
                {"category_id": "3", "category_name": "XXX Adult"}]

    def live_streams(self, category_id=None):
        return [dict(r) for r in ROWS if category_id in (None, r["category_id"])]


s = ProfileSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
reg = profiles.start(s, [], "Me")
kid = reg.add("Lisa", colour="pink", kids=True)
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


rows = Client().live_streams()
pump(30)
# A grown-up: every channel, as before.
for r in rows:
    w.favs.add("all", dict(r))
assert ids(w._chno_rows(rows)) == [1, 2, 3]
assert ids(w._favorite_channels()) == [1, 2, 3]
"""

_KIDS = r"""
# -- a kids profile ---------------------------------------------------------------
assert w.switch_profile(kid.id)
for r in rows:
    w.favs.add("all", dict(r))          # brought here somehow (a sync, an old copy)
w.switch_mode("live")
assert until(lambda: w.cat_list.count() >= 2), "the categories as the list reads them"
pump(20)
names = [w.cat_list.item(i).text() for i in range(w.cat_list.count())]
assert not any("XXX" in n for n in names), names
# The phone remote, the remote API and Home Assistant: their lists and plays.
assert ids(w._chno_rows(rows)) == [1], ids(w._chno_rows(rows))
assert ids(w._favorite_channels()) == [1]
listed = w._phone_channels(rows)
assert [c["id"] for c in listed["channels"]] == [1] and listed["favorites"] == [1], listed
for bad in rows[1:]:
    try:
        w._phone_do("_play", {"row": bad})
        raise AssertionError("the phone played a channel for adults")
    except ValueError:
        pass
assert ids(w._remote_do("_shown", {"rows": rows})) == [1]
# Asked over the network (the servers' threads, the window's own through
# their bridges), even before the window has read the categories.
from dopeiptv.ui.mw_remote import _Bridge


def on_server(fn):
    out = {}
    t = threading.Thread(target=lambda: out.update(r=fn()))
    t.start()
    while t.is_alive():
        app.processEvents()
        t.join(0.01)
    return out.get("r")


w._remote_bridge, w._phone_bridge = _Bridge(w._remote_do), _Bridge(w._phone_do)
w._kids_known().clear()
got = on_server(lambda: w._remote_call("channels", {}))
assert [c["id"] for c in got["channels"]] == [1], got
w._kids_known().clear()
got = on_server(lambda: w._phone_call("channels", {}))
assert [c["id"] for c in got["channels"]] == [1], got
w._events_favorites()
assert [r["stream_id"] for r in w._events_favs.values()] == [1], w._events_favs
# The main list's "All": nothing from the category for adults either.
w.all_items = [dict(r) for r in rows]
w._apply_filter()
shown = [w.list_model.item_at(i).get("stream_id") for i in range(w.list_model.rowCount())]
assert shown == [1], shown
# The guide of the list.
assert ids(w._guide_scope(False)[2]) == [1]
# What the workers filter with (the palette, programme search,
# TV mode, watch together): on a worker it reads the categories itself.
getattr(w, "_kids_known", dict)().clear()
keep, got = w._catalog_filter("live"), {}
t = threading.Thread(target=lambda: got.update(rows=[r for r in rows if keep(r)]))
t.start()
t.join()
assert ids(got["rows"]) == [1], got
# Multiview takes none of them.
added = []
w.add_to_multiview = lambda *a, **k: added.append(k.get("item", {}).get("stream_id"))
w._add_channel_to_multiview(rows[2], 0)
w._add_channel_to_multiview(rows[1], 0)
w._add_channel_to_multiview(rows[0], 0)
assert added == [1], added
# The command palette's channels and programmes: the kid's catalogue, and
# not the one kept from it once a grown-up is in use.
w._pal_catalogue = None
w._palette_load_catalogue()
assert until(lambda: getattr(w, "_pal_catalogue", None) is not None)
assert ids(w._pal_catalogue.chans) == [1]
assert w.switch_profile(MAIN_ID)
assert ids(w._chno_rows(rows)) == [1, 2, 3]
w._palette_load_catalogue()
assert until(lambda: getattr(w, "_pal_catalogue", None) is not None
             and ids(w._pal_catalogue.chans) == [1, 2, 3]), ids(w._pal_catalogue.chans)
print("KIDS_OK", flush=True)
os._exit(0)
"""

_LOCK = r"""
# -- "Who's watching?" waits for the PIN at start -------------------------------------
played, zapped, opened = [], [], []
w.play_live_channel = lambda it, *a, **k: played.append(it.get("stream_id"))
w._zap = lambda step: zapped.append(step)
w._open_palette = lambda *a, **k: opened.append("palette")
w._open_epg_guide = lambda *a, **k: opened.append("guide")
w.profiles.set_pin(MAIN_ID, "2468")
w._maybe_pick_profile_at_start()
pump(5)
assert w._profile_must_pick and w._profile_picker is not None
# The phone: nothing of the profile's listed or played, and it says why.
assert w._phone_channels(rows) == {"channels": [], "favorites": [], "_icons": {}}
st = w._phone_state()
assert st["locked"] is True and st["note"] == tr("phone_page_locked"), st
for action, params in (("_play", {"row": rows[0]}), ("next", {}), ("previous", {}),
                       ("toggle", {})):
    try:
        w._phone_do(action, params)
        raise AssertionError(f"the phone's {action} got past who is watching")
    except ValueError:
        pass
# The remote API: refused, plainly.
for action, params in (("play", {"channel": "News One"}), ("play", {"number": 1}),
                       ("_shown", {"rows": rows}), ("playpause", {}), ("next", {})):
    try:
        w._remote_do(action, params)
        raise AssertionError(f"the remote API's {action} got past who is watching")
    except ValueError as e:
        assert "who is watching" in str(e)
assert w._remote_do("status", {})["locked"] is True
# Home Assistant: no favourites listed, none played.
w._events_favorites()
assert w._events_favs == {}
# A command from outside (a second start, a dopeiptv:// link).
assert w.handle_command({"channel": "News One"}) == {"ok": False, "locked": True}
assert w.handle_command({"number": 1}) == {"ok": False, "locked": True}
# The window's own keys: the palette, the guide, number zapping, the media keys.
w._shortcuts["palette"].activated.emit()
w._shortcuts["epg_guide"].activated.emit()
w._shortcuts["zap_next"].activated.emit()
digit = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier, "1")
assert w._number_key(digit) is False
w._media_pressed = {}
w.mpris_command("next")
w.mpris_command("play")
pump(10)
assert played == [] and zapped == [] and opened == [], (played, zapped, opened)
# The PIN given (from the phone here): everything is there again.
w._profile_switch_remote({"id": MAIN_ID, "pin": "2468"})
assert not w._profile_must_pick
assert [c["id"] for c in w._phone_channels(rows)["channels"]] == [1, 2, 3]
assert [r["stream_id"] for r in w._events_favs.values()] == [1, 2, 3]
assert "locked" not in w._phone_state() and "locked" not in w._remote_do("status", {})
w._shortcuts["palette"].activated.emit()
assert opened == ["palette"]
print("LOCK_OK", flush=True)
os._exit(0)
"""


def _run(part: str) -> str:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _SETUP + part], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    return out.stdout + "\n" + out.stderr[-4000:]


def test_a_kids_profile_sees_and_plays_nothing_for_adults_anywhere():
    out = _run(_KIDS)
    assert "KIDS_OK" in out, out[-5000:]


def test_who_is_watching_at_start_holds_outside_tv_mode_too():
    out = _run(_LOCK)
    assert "LOCK_OK" in out, out[-5000:]


# -- the same, in pieces ---------------------------------------------------------------

_APP = None
_ROWS = [{"stream_id": 1, "name": "News One", "category_id": "1"},
         {"stream_id": 2, "name": "News Late", "category_id": "1", "is_adult": 1},
         {"stream_id": 3, "name": "Blue Room", "category_id": "3"}]


class _Client:
    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"},
                {"category_id": "3", "category_name": "For adults"}]

    def live_streams(self, category_id=None):
        return [dict(r) for r in _ROWS]

    def live_url(self, sid, fmt="ts"):
        return f"http://p/{sid}.ts"


def _app():
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # kept alive
    return _APP


def _kid_window():
    from PyQt6.QtCore import QThreadPool

    from dopeiptv.ui.mw_profiles import _ProfilesMixin

    class W(_ProfilesMixin):
        def __init__(self):
            self.client, self.overrides, self.pool = _Client(), None, QThreadPool()
            self.kids, self.refreshed = True, []

        def _profile_kids(self):
            return self.kids

        def _refresh_places(self, reason=""):     # the places' own pages
            self.refreshed.append(reason)

    return W()


def _wait(win, cond, secs=5.0):
    import time
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        win.pool.waitForDone(20)
        _APP.processEvents()
    return cond()


def test_on_the_windows_thread_a_row_waits_for_its_category_to_be_known():
    """The window's thread never asks the provider itself: a row whose
    category it does not know yet is held back, the categories are read on
    the pool, and the places are drawn again with what may be shown."""
    _app()
    w = _kid_window()
    keep = w._kids_keep("live")
    assert [r["stream_id"] for r in _ROWS if keep(r)] == []
    assert _wait(w, lambda: w.refreshed), "drawn again once they are in"
    assert [r["stream_id"] for r in _ROWS if w._kids_keep("live")(r)] == [1]
    assert w._kids_only(_ROWS) == [_ROWS[0]]
    # A row without a category; an episode whose series is for adults.
    assert w._kids_allows({"stream_id": 9, "name": "Clip"})
    assert not w._kids_allows({"_kind": "episode", "id": 5,
                               "_series_ctx": {"series_id": 4, "is_adult": "1"}})
    # Anyone else: every row, nothing to test.
    w.kids = False
    assert w._kids_keep("live") is None and w._kids_only(_ROWS) == _ROWS


def test_failover_watch_together_and_the_sidebar_search_keep_a_kids_profiles_limits():
    from PyQt6.QtWidgets import QListWidget

    from dopeiptv.core.together import Media
    from dopeiptv.services import failover
    from dopeiptv.services import together as matching
    from dopeiptv.ui.mw_search import _SearchMixin
    _app()
    pls = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    blue, late = {"name": "Blue Room"}, {"name": "News Late"}
    # Failover: the other source's copy for adults is no choice for a kid.
    assert failover.find_elsewhere(pls, "a", blue, make_client=lambda _p: _Client())
    for item in (blue, late):
        assert failover.find_elsewhere(pls, "a", item, make_client=lambda _p: _Client(),
                                       kids=True) is None
    assert failover.find_elsewhere(pls, "a", {"name": "News One"},
                                   make_client=lambda _p: _Client(), kids=True)
    # Watch together finds only what the lists show here.
    w = _kid_window()
    w._kids_note_cats("live", _Client().live_categories())
    keep = w._kids_keep("live")
    assert matching.locate(_Client(), Media("live", "Blue Room")) is not None
    assert matching.locate(_Client(), Media("live", "Blue Room"),
                           keep=lambda _kind, row: keep(row)) is None
    assert matching.locate(_Client(), Media("live", "News One"),
                           keep=lambda _kind, row: keep(row)) is not None

    # The sidebar's category search: no category for adults, none locked.
    class Ov:
        def is_hidden(self, mode, cid):
            return False

        def is_locked(self, mode, cid):
            return cid == "2"

        def display_name(self, mode, cid, name):
            return name

    class S(_SearchMixin):
        mode = "live"

        def __init__(self):
            self.cat_list, self.overrides = QListWidget(), Ov()
            self.parental = type("P", (), {"session_unlocked": False})()
            self._raw_categories = [{"category_id": "1", "category_name": "News"},
                                    {"category_id": "2", "category_name": "Late news"},
                                    {"category_id": "3", "category_name": "XXX news"}]

        def _profile_kids(self):
            return True

    s = S()
    s._render_cat_search("news", {})
    assert [s.cat_list.item(i).text() for i in range(s.cat_list.count())] == ["News"]
