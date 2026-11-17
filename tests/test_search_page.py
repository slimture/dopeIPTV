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


# -- what a hit does, on a window of stubs (no Qt window, no thread) ------------------

class _Settings:
    def __init__(self):
        self.d = {}

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):
        self.d[key] = value


class _Playlists:
    def __init__(self, playlists, active):
        self.items = {p["id"]: p for p in playlists}
        self.active_id = active

    def get(self, pid):
        return self.items.get(pid)

    def active(self):
        return self.items.get(self.active_id)

    def playlists(self):
        return list(self.items.values())


class _Nordic:
    def live_url(self, sid, fmt="ts"):
        return f"http://nordic/live/{sid}.{fmt}"


def _stub_window(monkeypatch):
    from dopeiptv.core.stores import CategoryOverrides, ChannelOverrides
    from dopeiptv.services import search_sources as ss
    from dopeiptv.ui import mw_failover, mw_search_all

    class Parental:
        session_unlocked = False

    class W(mw_search_all._SearchAllMixin, mw_failover._FailoverMixin):
        def __init__(self):
            self.settings = _Settings()
            self.playlist_store = _Playlists(
                [{"id": "a", "name": "Pirat", "kind": "xtream", "options": {"ua": "a"}},
                 {"id": "b", "name": "Nordic", "kind": "xtream", "options": {"ua": "b"}}], "a")
            self.overrides = CategoryOverrides(self.settings, "category_overrides_a")
            self.channel_ov = ChannelOverrides(self.settings, "channel_overrides_a")
            self.parental = Parental()
            self.client = self.xmltv = self.pool = None
            self._sa_sources = ss.Sources(make_client=lambda pl: _Nordic())
            self.here, self.toasts, self.status, self.queries = [], [], [], []
            self.refuse = False
            self._last_playback = None

        def _show_toast(self, text, ms=0):
            self.toasts.append(text)

        def _own(self, settings):
            return settings

        def _set_status(self, text, error=False, emphasis=False):
            self.status.append(text)

        def play_live_channel(self, row):
            self.here.append(row["stream_id"])

        def _start_playback(self, url, title, icon, key, kind, record=True, **kw):
            if self.refuse:         # a recording's question answered Keep watching
                return
            self._failover_leave(url, key)
            self._last_playback = {"url": url, "key": key, "kind": kind}

    jobs = []
    monkeypatch.setattr(mw_search_all, "run_async",
                        lambda pool, fn, ok, fail=None, *a: jobs.append((fn, ok)))
    return W(), jobs


def _hit(source_id, sid=8):
    from dopeiptv.core import search_all as sa
    return sa.Hit("channels", sa.Source(source_id, source_id.upper()),
                  {"stream_id": sid, "name": "Vegas"}, "Vegas")


def test_a_hit_of_a_source_removed_since_is_never_played_from_the_one_in_use(monkeypatch):
    from dopeiptv.i18n import tr
    w, jobs = _stub_window(monkeypatch)
    w.playlist_store.items.pop("b")                 # removed while its results show
    w._sa_play_channel(_hit("b"))
    assert w.here == [] and jobs == []
    assert w.toasts == [tr("sa_source_gone", source="B")]
    later = _hit("b")                                 # nor a reminder kept for it
    later.group, later.programme = "programmes", {
        "title": "Vegas Report", "start_timestamp": 4_000_000_000,
        "stop_timestamp": 4_000_003_600}
    w._sa_remind(later)
    assert "epg_reminders_b" not in w.settings.d
    # The one in use plays here; with no source of the user's (the demo),
    # its channels are the client's in use.
    w._sa_play_channel(_hit("a", 1))
    assert w.here == [1]
    w.playlist_store = _Playlists([], None)
    w._sa_play_channel(_hit("-", 2))
    assert w.here == [1, 2]


def test_another_sources_address_that_comes_late_never_takes_over(monkeypatch):
    from dopeiptv.core import nerd
    w, jobs = _stub_window(monkeypatch)
    try:
        w._sa_play_channel(_hit("b"))
        fn, ok = jobs.pop()
        w._start_playback("http://pirat/live/1.ts", "Pirat 1", None, 1, "live")  # picked meanwhile
        ok(fn())
        assert w._last_playback["key"] == 1 and w._failover_source is None
        # Nothing else meanwhile: it plays, from Nordic.
        w._sa_play_channel(_hit("b"))
        fn, ok = jobs.pop()
        ok(fn())
        assert w._last_playback == {"url": "http://nordic/live/8.ts", "key": "failover:b:8",
                                    "kind": "live"}
        # A film of Nordic's: switched to first - and something played while
        # that source answers is not taken over either.
        switched = []
        w.switch_playlist = lambda pid, then=None: switched.append(then)
        w._links_later = lambda *a: False
        w._item_key = lambda row: f"m{row['stream_id']}"
        w.client = _Nordic()
        w.client.vod_url = lambda sid, ext=None: f"http://nordic/movie/{sid}.mp4"
        film = _hit("b", 60)
        film.group = "movies"
        w._sa_play_movie(film)
        w._start_playback("http://pirat/live/3.ts", "Pirat 3", None, 3, "live")
        switched.pop()()
        assert w._last_playback["key"] == 3
    finally:
        nerd.set_source(None)


def test_another_sources_channel_not_played_leaves_what_plays_as_it_was(monkeypatch):
    from dopeiptv.core import nerd
    w, jobs = _stub_window(monkeypatch)
    try:
        nerd.set_source(w.playlist_store.active())
        w._start_playback("http://pirat/live/1.ts", "Pirat 1", None, 1, "live")
        w.refuse = True
        w._sa_play_channel(_hit("b"))
        fn, ok = jobs.pop()
        ok(fn())
        # Pirat's channel goes on: its own options, counted as Pirat's.
        assert w._last_playback["key"] == 1
        assert w._failover_source is None and w._failover_key is None
        assert nerd.source() == {"ua": "a", "_kind": "xtream"}
    finally:
        nerd.set_source(None)


def test_results_found_before_a_sources_rules_changed_are_not_acted_on(monkeypatch):
    w, _jobs = _stub_window(monkeypatch)

    class Page:
        shown = "results"

        def show_results(self, results, keep_place=True):
            self.shown = results

        def query(self):
            return "vegas"

    w._search_page = Page()
    w._sa_query = lambda text, keep_place=False: w.queries.append(text)
    w._sa_made_for = w._sa_made_key()
    assert w._sa_still_allowed()
    w.overrides.update("live", "sp", hidden=True)    # Sport hidden since
    assert not w._sa_still_allowed()
    assert w._search_page.shown is None and w.queries == ["vegas"]
    w._sa_made_for = w._sa_made_key()
    w.settings.setValue("channel_overrides_b", '{"live": {"8": {"hidden": true}}}')
    assert not w._sa_still_allowed()                 # a channel of Nordic's hidden by hand


class _Timer:
    """A single-shot timer that fires when told to."""

    def __init__(self):
        self.interval, self.single, self.active, self.slots = None, None, False, []

    def setSingleShot(self, single):
        self.single = single

    def setInterval(self, ms):
        self.interval = ms

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    @property
    def timeout(self):
        return self

    def connect(self, slot):
        self.slots.append(slot)

    def fire(self):
        self.active = False
        for slot in self.slots:
            slot()


def test_the_guide_weeks_go_a_while_after_the_page_is_left(monkeypatch):
    w, _jobs = _stub_window(monkeypatch)
    timer = _Timer()
    w._sa_new_timer = lambda: timer
    released, kept = [], []
    w._sa_sources.release_guides = lambda: released.append(1) or 0
    w._sa_sources.keep = lambda signatures: kept.append(sorted(signatures))
    w._sa_page_shown(False)                          # left
    assert timer.active and timer.single and timer.interval == 5 * 60 * 1000
    w._sa_page_shown(True)                           # back in time: kept
    assert not timer.active and released == []
    w._sa_page_shown(False)
    timer.fire()
    assert released == [1]
    w._search_profile_changed()                      # another profile: at once
    assert released == [1, 1]
    w.playlist_store.items.pop("b")                  # a source removed (or edited)
    w._search_sources_changed()
    assert kept == [["a"]]


def _stub_page():
    from PyQt6.QtCore import QObject

    class Page(QObject):
        shown, busy = "results", None

        def show_results(self, results, keep_place=True):
            self.shown = results

        def set_busy(self, busy):
            self.busy = busy

        def query(self):
            return "vegas"

    return Page()


def test_away_from_the_page_nothing_more_is_read(monkeypatch):
    w, _jobs = _stub_window(monkeypatch)
    w._search_page = page = _stub_page()
    prepared = []
    w._sa_prepare = lambda plans=None: prepared.append(1)
    w._sa_query = lambda text, keep_place=False: w.queries.append(text)
    w._search_page_showing = lambda: False
    w._sa_source_in("b")                             # its lists came while away
    assert prepared == [] and w.queries == [] and page.busy is False
    w._search_page_showing = lambda: True
    w._sa_source_in("b")
    assert prepared == [1] and w.queries == ["vegas"]


def test_lock_now_with_the_page_open_finds_again_at_once(monkeypatch):
    w, _jobs = _stub_window(monkeypatch)
    w._search_page = page = _stub_page()
    w._sa_query = lambda text, keep_place=False: w.queries.append(text)
    w._search_page_visible = lambda: True
    w.parental.session_unlocked = True
    w._sa_made_for = w._sa_made_key()
    w._search_rules_check()                          # nothing changed: as it is
    assert page.shown == "results" and w.queries == []
    w.parental.session_unlocked = False              # Lock now
    w._search_rules_check()
    assert page.shown is None and w.queries == ["vegas"]


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
    # Still so once what is queued has run: the window's start reloads its
    # list once more (a timer), and a list seen for a moment went empty.
    end = time.time() + secs
    while time.time() < end:
        if cond():
            pump(2)
            if cond():
                return
        pump(2)
        time.sleep(0.01)
    assert cond(), "timed out"


tmp = tempfile.mkdtemp()
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
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
assert list(w.nav_btns)[5] == "search" and nav.text() == tr("nav_search")   # the last of the upper places
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
# Left: the guide weeks go a while after, unless it comes back first.
assert w._sa_release.isActive() and w._sa_release.interval() == 5 * 60 * 1000
w._open_search_page()
pump()
assert not w._sa_release.isActive()
assert page.query() == "star trek" and page.results is not None
from dopeiptv.core import privacy
privacy.set_private(True)
w.switch_mode("live")
pump()
w._open_search_page()
pump()
assert page.query() == "" and page.results is None
privacy.set_private(False)

# -- the lock shut with the page open (Lock now): a locked category's rows go at once ---
assert store.active_id == "b"                      # Nordic's since its film
w.overrides.update("live", "n", locked=True)
w.parental.session_unlocked = True


def channels():
    return [h.title for h in page.results.hits["channels"]] if page.results else []


w._open_search_page("vegas")
settle(lambda: "Vegas Nordic" in channels())
w.parental.lock_session()
w._load_categories()
settle(lambda: page.results is not None and "Vegas Sport" in channels()
       and "Vegas Nordic" not in channels())
w.overrides.update("live", "n", locked=False)

# -- its key while the video fills the screen: the page in the window, shown ------------
w.switch_mode("live")
pump()
w._enter_docked_fullscreen()
pump()
assert w._player_fs
w._shortcut_run("epg_search")
settle(lambda: page.isVisible())
assert not w._player_fs and w._search_page_showing()
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
