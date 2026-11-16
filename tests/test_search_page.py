"""The Search page in the window (ui/search_page.py, ui/mw_search_all.py):
its entry, its key (the guide search's, a key of the user's carried over),
the guide's Search button and the palette's "Search everywhere for …" all
open it in the middle pane; typing finds channels, programmes, films and
series in two sources, in groups, each row naming its source; a channel
plays and the page stays - one of the other source without switching to
it, the status line saying where it is from; a programme later sets a
reminder in its own source's reminders; a film of the other source is
played once that source is switched to, the page and its results kept; a
series opens its page and Back comes here, results and scroll as they
were; Esc empties the field first, then goes back where you were.

Runs in a subprocess like the other MainWindow tests; the child prints a
marker when every check passed."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from dopeiptv.core import shortcut_keys

pytest.importorskip("PyQt6.QtWidgets")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_search_key_is_the_guide_searchs_and_a_users_key_carries_over():
    assert shortcut_keys.DEFAULTS["epg_search"] == "Ctrl+Shift+F"
    assert shortcut_keys.LABELS["epg_search"] == "sc_search_all"
    from dopeiptv.i18n import tr
    assert tr("sc_search_all") == "Search everywhere"
    stored = {shortcut_keys.SETTING: '{"epg_search": "Ctrl+Alt+S"}'}

    class S:
        def value(self, key, default=None):
            return stored.get(key, default)

    assert shortcut_keys.from_settings(S())["epg_search"] == "Ctrl+Alt+S"


_CHILD = r"""
import os, sys, tempfile, time
from array import array
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv import i18n
from dopeiptv.core import shortcut_keys
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.services import search_sources as ss
from dopeiptv.services.reminders import ReminderStore
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import theme

i18n.set_language("en")
NOW = int(time.time())


def sched(*progs):
    return (array("q", [p[0] for p in progs]), array("q", [p[1] for p in progs]),
            tuple(p[2] for p in progs), tuple(p[3] for p in progs))


class Pirat(DemoClient):
    def live_categories(self):
        return [{"category_id": "sp", "category_name": "Sport"},
                {"category_id": "x", "category_name": "Hidden"}]

    def live_streams(self, cat=None):
        rows = [{"stream_id": 1, "name": "Vegas Sport", "category_id": "sp",
                 "epg_channel_id": "vegas", "stream_icon": ""},
                {"stream_id": 2, "name": "Vegas Hidden", "category_id": "x"},
                {"stream_id": 3, "name": "Star Channel", "category_id": "sp",
                 "epg_channel_id": "star"}]
        return [r for r in rows if cat in (None, r["category_id"])]

    def live_url(self, sid, fmt="ts"):
        return f"http://pirat/live/{sid}.{fmt}"

    def vod_categories(self):
        return [{"category_id": "m", "category_name": "Action"}]

    def vod_streams(self, cat=None):
        return [{"stream_id": 50, "name": "Star Trek", "category_id": "m",
                 "container_extension": "mkv", "year": "2009"}]

    def vod_url(self, sid, ext=None):
        return f"http://pirat/movie/{sid}.{ext}"

    def series_categories(self):
        return [{"category_id": "s", "category_name": "Drama"}]

    def series_list(self, cat=None):
        return [{"series_id": 70 + i, "name": f"Star Show {i}", "category_id": "s"}
                for i in range(12)]

    def series_info(self, sid):
        return {"info": {"name": "Star Show"}, "episodes": {"1": [
            {"id": "900", "episode_num": 1, "title": "Pilot", "container_extension": "mkv"}]}}


class Nordic(DemoClient):
    asked = []

    def authenticate(self):
        return {"user_info": {"auth": 1}}

    def live_categories(self):
        return [{"category_id": "n", "category_name": "News"}]

    def live_streams(self, cat=None):
        return [{"stream_id": 8, "name": "Vegas Nordic", "category_id": "n",
                 "epg_channel_id": "vn"}]

    def live_url(self, sid, fmt="ts"):
        Nordic.asked.append(sid)
        return f"http://nordic/live/{sid}.{fmt}"

    def vod_categories(self):
        return []

    def vod_streams(self, cat=None):
        return [{"stream_id": 60, "name": "Vegas Vacation", "container_extension": "mp4"}]

    def vod_url(self, sid, ext=None):
        return f"http://nordic/movie/{sid}.{ext}"

    def series_categories(self):
        return []

    def series_list(self, cat=None):
        return []


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)
        if w._sa_pools:
            for p in w._sa_pools:
                p.waitForDone(5)


def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        pump(2)
        time.sleep(0.01)
    pump(2)
    assert cond(), "timed out"


tmp = tempfile.mkdtemp()
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("home_enabled", "false")
# A key of the user's for the old guide search: the Search page's now.
s.setValue(shortcut_keys.SETTING, '{"epg_search": "Ctrl+Alt+S"}')
store = PlaylistStore(s)
store.add({"id": "a", "name": "Pirat", "kind": "xtream", "server": "http://pirat.invalid",
           "username": "u", "password": "p"})
store.add({"id": "b", "name": "Nordic", "kind": "xtream", "server": "http://nordic.invalid",
           "username": "u", "password": "p"})
store.set_active("a")
s.setValue("category_overrides_a", '{"live": {"x": {"hidden": true}}}')
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
w = mw.MainWindow(Pirat(), s, store)
w.cover.resolver = None
nordic = Nordic()
mw.make_client = lambda pl: nordic if pl.get("id") == "b" else Pirat()

# The guide in use (in memory) and Nordic's on disk.
mine = sched((NOW - 600, NOW + 1800, "Vegas Night Live", "on now"),
             (NOW + 7200, NOW + 9000, "Vegas Tomorrow", "later"))
w.xmltv.is_loaded = lambda: True
w.xmltv.schedule = lambda ch: mine if ch.get("epg_channel_id") == "vegas" else None
open(os.path.join(tmp, "epg_b.xml"), "wb").write(b"x")


class DiskGuide:
    def __init__(self, path):
        pass

    def load_cached(self):
        return True

    def schedule(self, ch):
        if ch.get("epg_channel_id") == "vn":
            return sched((NOW + 3600, NOW + 5400, "Vegas Report", ""))
        return None


w._sa_sources = ss.Sources(make_client=lambda pl: nordic,
                           guide_path=lambda pid: os.path.join(tmp, f"epg_{pid}.xml"),
                           open_guide=DiskGuide)
played = []
w.player.play = lambda url, *a, **k: played.append(url) or True
w._choice_dialog = lambda *a, **k: 0                  # the resume question: from the start
w.resize(1280, 820)
w.show()
w.activateWindow()
pump(30)
settle(lambda: w.isActiveWindow())           # keys and focus need it active
w.switch_mode("live")
settle(lambda: w.list_model.rowCount() > 0)

# -- its key (the user's own for the guide search), its entry ------------------------
assert w.shortcut_sequence("epg_search") == "Ctrl+Alt+S"
nav = w.nav_btns["search"]
assert list(w.nav_btns)[:2] == ["home", "search"] and nav.text() == tr("nav_search")
w._shortcut_run("epg_search")
pump()
page = w._search_page
assert w._search_page_showing() and page.isVisible() and nav.isChecked()
assert not w._list_page.isVisible() and w._det.isVisible()
settle(lambda: page.field.hasFocus())
w._on_escape()                                         # nothing typed: back
pump()
assert not w._search_page_showing() and w.nav_btns["live"].isChecked()
nav.click()
pump()
assert w._search_page_showing()

# -- typing finds every source's, in groups, each row naming its source ---------------
QTest.keyClicks(page.field, "vegas")
settle(lambda: page.results is not None and page.results.totals["programmes"] >= 3
       and page.results.totals["movies"] == 1)
res = page.results
names = [(h.title, h.source.name) for h in res.hits["channels"]]
assert names == [("Vegas Sport", "Pirat"), ("Vegas Nordic", "Nordic")], names
assert "Vegas Hidden" not in [t for t, _ in names]         # its category is hidden
progs = res.hits["programmes"]
assert [h.title for h in progs][0] == "Vegas Night Live" and progs[0].on_air(time.time())
assert {h.source.name for h in progs} == {"Pirat", "Nordic"}
assert [h.title for h in res.hits["movies"]] == ["Vegas Vacation"]
from dopeiptv.ui.search_page import second_line
assert second_line(res.hits["channels"][0], time.time()) == "Pirat · Sport"
line = second_line(progs[0], time.time())
assert line.startswith("Vegas Sport · Pirat · "), line
rows = page.model.rows
heads = [r.group for r in rows if r.kind == "head"]
assert heads == ["channels", "programmes", "movies"], heads
assert page.view.first_row() == 1

# -- a channel plays; the page stays ---------------------------------------------------
QTest.keyClick(page.field, Qt.Key.Key_Down)
assert page.view.hasFocus() and page.current_hit().title == "Vegas Sport"
QTest.keyClick(page.view, Qt.Key.Key_Return)
pump()
assert played[-1] == "http://pirat/live/1.ts", played
assert w._search_page_showing() and page.isVisible()
assert w._last_playback["kind"] == "live"

# -- the other source's: not switched to, from Nordic ------------------------------------
QTest.keyClick(page.view, Qt.Key.Key_Down)
assert page.current_hit().title == "Vegas Nordic"
QTest.keyClick(page.view, Qt.Key.Key_Return)
settle(lambda: played[-1] == "http://nordic/live/8.ts")
assert store.active_id == "a" and Nordic.asked == [8]
assert w._last_playback["key"] == "failover:b:8"
assert w._playing_elsewhere() == "Nordic"
assert w._status_playing("Vegas Nordic") == tr("status_playing_from", title="Vegas Nordic",
                                                source="Nordic")
assert w._search_page_showing()
# Down goes on into the next group, past its heading; Up from the first
# row goes back to the field, Tab between the two.
QTest.keyClick(page.view, Qt.Key.Key_Down)
assert page.current_hit() is not None and page.current_hit().group == "programmes"
QTest.keyClick(page.view, Qt.Key.Key_Tab)
assert page.field.hasFocus()
QTest.keyClick(page.field, Qt.Key.Key_Tab)
assert page.view.hasFocus()
page.view.setCurrentIndex(page.model.index(page.view.first_row(), 0))
QTest.keyClick(page.view, Qt.Key.Key_Up)
assert page.field.hasFocus()

# -- a programme later: a reminder in its own source's reminders ---------------------------
later = next(h for h in progs if h.source.name == "Nordic")
page.activated.emit(later)
pump()
assert ReminderStore(w._own(w.settings), "b").has(8, NOW + 3600)
assert not w.reminders.has(8, NOW + 3600)
# It fires whichever source is in use, and Watch now plays it from Nordic.
due = w._other_sources_due(NOW + 3600)
assert [r["_source"]["id"] for r in due] == ["b"]

# -- a series: its page, and Back here as it was ---------------------------------------------
page.set_query("star")
settle(lambda: page.results is not None and page.results.totals["series"] == 12)
page.set_filter("series")
pump()
assert sum(1 for r in page.model.rows if r.kind == "hit") == 12
page.view.setCurrentIndex(page.model.index(9, 0))
page.view.verticalScrollBar().setValue(page.view.verticalScrollBar().maximum())
pump()
before = (page.view.currentIndex().row(), page.view.verticalScrollBar().value(), page.query())
hit = page.current_hit()
page.activated.emit(hit)
settle(lambda: w._series_page_showing() and bool(w._sp_seasons))
assert nav.isChecked()
w._on_escape()                                     # Back
pump()
assert w._search_page_showing() and page.isVisible()
assert (page.view.currentIndex().row(), page.view.verticalScrollBar().value(),
        page.query()) == before, before

# -- a film of the other source: switched to, then played; the page stays ---------------------
page.set_query("vacation")
settle(lambda: page.results is not None and page.results.totals["movies"] == 1)
page.activated.emit(page.results.hits["movies"][0])
settle(lambda: played[-1] == "http://nordic/movie/60.mp4")
assert store.active_id == "b" and w._last_playback["kind"] == "movie"
assert w._search_page_showing() and page.results is not None
assert page.query() == "vacation"

# -- Esc: the field emptied first, then back to the list ---------------------------------------
page.field.setFocus()
QTest.keyClick(page.field, Qt.Key.Key_Escape)
pump()
assert page.query() == "" and w._search_page_showing()
QTest.keyClick(page.field, Qt.Key.Key_Escape)
pump()
assert not w._search_page_showing() and w._list_page.isVisible()

# -- the palette's row and the guide's button ---------------------------------------------------
e = w._palette_everywhere("  star   trek ")[0]
assert e.payload == ("search_all", "star trek") and "star trek" in e.text
assert w._palette_everywhere("s") == []
w._palette_then("search_all", "star trek")
pump()
assert w._search_page_showing() and page.query() == "star trek"
w.switch_mode("live")
pump()
assert not w._search_page_showing()
w._open_epg_guide()
pump()
w._guide_grid._open_search()
pump()
assert w._search_page_showing()

# -- kept for the next visit - but not while viewing privately --------------------------------
assert page.query() == "star trek"
w.switch_mode("live")
w._open_search_page()
pump()
assert page.query() == "star trek" and page.results is not None
from dopeiptv.core import privacy
privacy.set_private(True)
w.switch_mode("live")
pump()
w._open_search_page()
pump()
assert page.query() == "" and page.results is None
privacy.set_private(False)
print("SEARCH_PAGE_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_search_page_in_the_window(tmp_path):
    for _attempt in (1, 2):
        out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                             env=_child_env(tmp_path), capture_output=True,
                             text=True, timeout=180)
        if "SEARCH_PAGE_OK" in out.stdout:
            return
        if "AssertionError" in out.stderr:
            break
    raise AssertionError(out.stdout + out.stderr[-4000:])
