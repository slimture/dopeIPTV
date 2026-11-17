"""The Series place (core/series_continue.py, ui/series_place.py,
ui/mw_place_series.py): Continue from what the app keeps - the series
page's cache for the next episode, else a resume point, else the last one
watched; never a series_info request for the row - and the place's cards
filled only as they paint. In the window: the source's categories and the
grid, Popular right now matched against the source (ranks, a category's
own, hidden when nothing matches or TMDB is off), Continue playing its
episode where it got to without the resume question with the place kept,
the grid / list switch remembered, the sort (recently updated first where
the source says when), a series' page opened from the grid and Back to the
grid exactly as it was, a followed series' news, the filter, the
Following category, a kids profile, private viewing and a locked
category."""
import os
import subprocess
import sys
import time

import pytest

from dopeiptv.core import series_continue as sc
from dopeiptv.core import series_view as sv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ep(eid, num, season):
    return {"id": str(eid), "episode_num": num, "season": season, "title": f"Ep {num}",
            "container_extension": "mkv", "info": {"duration_secs": 2700}}


def _resume(eid, season, num, sid, pos, dur=2700, ts=100, name="Show"):
    return {"id": str(eid), "season": season, "episode_num": num, "name": f"{name} · S{season} * E{num}",
            "_kind": "episode", "_resume_pos": pos, "_resume_dur": dur, "_ts": ts,
            "_series_ctx": {"series_id": sid, "name": name, "category_id": "6"}}


def _hist(eid, season, num, sid, when, name="Show"):
    return {"_kind": "episode", "_key": str(eid), "_url": f"http://x/{eid}.mkv",
            "name": f"{name} · S{season} * E{num} - Ep {num}", "_watched_at": when,
            "_series_ctx": {"series_id": sid, "name": name, "category_id": "6"}}


# -- Continue, worked out from what the app keeps (no Qt) ---------------------------

def test_continue_takes_the_next_episode_from_the_cache_else_the_resume_point():
    eps = {"1": [_ep(1001, 1, 1), _ep(1002, 2, 1)], "2": [_ep(2001, 1, 2), _ep(2002, 2, 2)]}
    cached = {"7": [e for s in eps.values() for e in s]}
    asked = []

    def episodes_of(series):
        asked.append(series["series_id"])
        return cached.get(str(series["series_id"]))

    watched = {"1001", "1002", "2001"}

    def state_of(ep, _series):
        prog = sv.Progress(1200, 2700, 50) if ep.get("id") == "2002" else sv.Progress()
        return ep.get("id") in watched, prog

    rows = [_resume(2002, 2, 2, 7, 1200, ts=300, name="Seven"),
            _resume(51, 3, 4, 8, 600, ts=200, name="Eight")]
    got = sc.gather(rows, [], episodes_of=episodes_of, state_of=state_of)
    assert [e.series_id for e in got] == ["7", "8"]            # the newest first
    seven, eight = got
    # From the cache: the page's own next episode (in progress), with its seasons.
    assert seven.known and seven.resume and seven.episode is cached["7"][3]
    assert [s.number for s in seven.seasons] == [1, 2] and seven.progress.pos == 1200
    # Not cached: the resume point's episode, from where it got to; the series'
    # name the row put in front of the episode's own taken off again.
    assert not eight.known and eight.resume and eight.episode["id"] == "51"
    assert eight.progress.minutes_left == 35 and eight.episode["name"] == "S3 * E4"
    assert eight.season_episode() == (3, 4)
    assert asked == [7, 8]                                     # only what is kept


def test_continue_falls_back_to_the_last_watched_and_leaves_out_what_is_done():
    eps = [_ep(1001, 1, 1), _ep(1002, 2, 1)]
    hist = [_hist(1002, 1, 2, 7, "2026-11-10T20:00:00", "Seven"),     # finished, cached
            _hist(3005, 2, 5, 9, "2026-11-12T20:00:00", "Nine"),      # watched, not cached
            _hist(4001, 1, 1, 10, "2026-11-13T20:00:00", "Ten"),      # barely begun
            _hist(5001, 1, 1, 11, "2026-11-11T20:00:00", "Eleven")]   # hidden
    watched = {"1001", "1002", "3005"}

    def state_of(ep, _series):
        return ep.get("id") in watched, sv.Progress()

    got = sc.gather([], hist, episodes_of=lambda s: eps if str(s["series_id"]) == "7" else None,
                    state_of=state_of, keep=lambda s: str(s["series_id"]) != "11")
    assert [e.series_id for e in got] == ["9"], [e.series_id for e in got]
    nine = got[0]
    # The last one watched, read from History's name: its next is not known.
    assert nine.watched and not nine.known and not nine.resume
    assert nine.season_episode() == (2, 5) and nine.episode["id"] == "3005"
    # The next one known from the cache: Up next, not in progress.
    watched.discard("1002")
    hist[0]["_watched_at"] = "2026-11-14T20:00:00"
    watched.add("1001")
    got = sc.gather([], hist, episodes_of=lambda s: eps if str(s["series_id"]) == "7" else None,
                    state_of=state_of)
    assert got[0].series_id == "7" and got[0].known and got[0].episode["id"] == "1002"
    assert not got[0].resume and got[0].progress.pos == 0
    # At most the limit, the newest first.
    many = [_resume(i, 1, 1, 100 + i, 600, ts=i) for i in range(30)]
    got = sc.gather(many, [], episodes_of=lambda s: None,
                    state_of=lambda e, s: (False, sv.Progress()), limit=5)
    assert [e.series_id for e in got] == ["129", "128", "127", "126", "125"]


# -- the place's cards, filled as they paint (Qt, no window) -------------------------

@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    a = QApplication.instance() or QApplication([])
    from dopeiptv.ui import theme as th
    th.apply_theme(theme="graphite")
    return a


def _spin(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def test_twenty_thousand_cards_fill_only_what_paints(app):
    from dopeiptv.ui.series_place import SeriesCard, SeriesPlace
    place = SeriesPlace(None, "poster")
    filled: list[tuple[str, bool]] = []

    def fill(card, full):
        filled.append((card.key, full))
        if full:
            card.line = "2026"

    place.grid.fill = fill
    cards = [SeriesCard(key=f"s:{i}", title=f"Show {i}", data={"series_id": i})
             for i in range(20000)]
    t0 = time.perf_counter()
    place.grid.set_items(cards)
    place.resize(900, 800)
    place.show()
    _spin(app)
    place.grab()
    took = time.perf_counter() - t0
    keys = {k for k, _f in filled}
    assert 0 < len(keys) < 80, len(keys)                    # not 20,000
    assert took < 3.0, took
    # Painted again: the picture and line not worked out again.
    n_full = sum(1 for _k, f in filled if f)
    place.grab()
    assert sum(1 for _k, f in filled if f) == n_full
    # Posters found since: worked out again, for what is on screen only.
    place.grid.refresh_art()
    place.grab()
    again = {k for k, f in filled[len(filled) // 2:] if f}
    assert again and len(again) < 80
    # Scrolled to the end: the cards there, not the ones on the way.
    sb = place.area.verticalScrollBar()
    sb.setValue(sb.maximum())
    _spin(app)
    place.grab()
    keys = {k for k, _f in filled}
    assert "s:19999" in keys and "s:10000" not in keys
    place.close()


def test_a_plain_click_opens_ctrl_gathers(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from dopeiptv.ui.series_place import SeriesCard, SeriesGrid
    grid = SeriesGrid(None, "list")
    grid.set_items([SeriesCard(key=f"s:{i}", title=f"Show {i}") for i in range(6)])
    grid.resize(500, 500)
    grid.show()
    _spin(app)
    opened = []
    grid.activated.connect(opened.append)
    pos = grid.visualRect(grid.model_.index(1, 0)).center()
    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert [c.key for c in opened] == ["s:1"]
    pos = grid.visualRect(grid.model_.index(3, 0)).center()
    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier, pos)
    assert [c.key for c in opened] == ["s:1"]               # gathered, not opened
    assert [c.key for c in grid.selected_items()] == ["s:1", "s:3"]
    grid.close()


# -- in the window ------------------------------------------------------------------

_CHILD = r"""
import json, os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.core import privacy
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui import mw_place_series

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
asked_resume = []
w._choice_dialog = lambda *a, **k: asked_resume.append(a) or 0

def settle(cond, secs=12, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what

def idle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

c = w.client
cats = [{"category_id": "5", "category_name": "Drama"},
        {"category_id": "6", "category_name": "Comedy"},
        {"category_id": "9", "category_name": "XXX Night"}]
shows = [{"series_id": 100 + i, "name": f"Show {i:02d}", "category_id": "6" if i % 2 else "5",
          "cover": "", "releaseDate": "2019-04-01", "rating": "8.2",
          "last_modified": str(1_700_000_000 + (i * 7919) % 60)} for i in range(60)]
shows.append({"series_id": 900, "name": "Late Show", "category_id": "9", "cover": "",
              "last_modified": "1800000000"})
c.series_categories = lambda: cats
listed = []
def series_list(cat=None):
    listed.append(cat)
    return [s for s in shows if cat in (None, s["category_id"])]
c.series_list = series_list
def season(n, count):
    return [{"id": str(n * 1000 + e), "episode_num": e, "title": f"Ep {e}",
             "container_extension": "mkv", "info": {"duration_secs": 2700}}
            for e in range(1, count + 1)]
info = {"info": {"name": "Show 31"}, "episodes": {"1": season(1, 3), "2": season(2, 4)}}
asked = []
def series_info(sid):
    asked.append(str(sid))
    return info
c.series_info = series_info
c.episode_url = lambda i, ext=None: f"http://127.0.0.1:9/series/{i}.{ext or 'mkv'}"

w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0, what="first list")
idle(1.0)

# -- the place where the list was, as the sidebar's Series entry shows it -----------
assert w._show_place("series") is True                      # the sidebar's Series entry
settle(lambda: w.mode == "series" and w._place_series_active(), what="the place")
place = w._place_series
grid = place.grid
settle(lambda: w.list_model.rowCount() == 30 and grid.model_.rowCount() == 30, what="cards")
assert not w.listw.isVisible() and place.isVisible() and w.count_lbl.isVisible()
assert w.nav_btns["series"].isChecked()
assert place.header.title.text() == "Drama", place.header.title.text()
# Recently updated first where the source says when; said in the count line.
order = [c.data["series_id"] for c in grid.items()]
want = [s["series_id"] for s in sorted((s for s in shows if s["category_id"] == "5"),
                                        key=lambda s: int(s["last_modified"]), reverse=True)]
assert order == want, (order[:5], want[:5])
assert place.header.count.text() == tr("count_series", n=30) + " · " + tr("place_sort_updated")
assert place.header.sort_key() == "recent"
# Nothing of Popular (no TMDB) and nothing to continue: the rows stay away.
assert place.popular.isHidden() and place.cont.isHidden()

# -- the sort: the category's own, as the list keeps it -------------------------------
place.header._sort_picked("alpha_asc")
settle(lambda: [c.title for c in grid.items()][:2] == ["Show 00", "Show 02"], what="A-Z")
assert settings.value(w._sort_setting_key()) == "alpha_asc"
assert place.header.count.text().endswith("A→Z")
place.header._sort_picked("recent")
settle(lambda: [c.data["series_id"] for c in grid.items()] == want, what="recent again")

# -- grid / list, remembered ------------------------------------------------------------
place.header.switch.buttons[1].click()
assert grid.layout_kind == "list" and settings.value(mw_place_series.VIEW_KEY) == "list"
w.switch_mode("live")
settle(lambda: not w._place_series_active() and w.listw.isVisible(), what="TV's list")
w._on_nav_clicked("series")
settle(lambda: w._place_series_active() and grid.model_.rowCount() == 30, what="back")
assert grid.layout_kind == "list" and place.header.view_key() == "list"
place.header.switch.buttons[0].click()
assert grid.layout_kind == "poster" and settings.value(mw_place_series.VIEW_KEY) == "poster"

# -- the other category, picked in the left column ---------------------------------------
row = next(i for i in range(w.cat_list.count())
           if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "6")
w.cat_list.setCurrentRow(row)
settle(lambda: place.header.title.text() == "Comedy" and grid.model_.rowCount() == 30, what="Comedy")

# -- a series' page from the grid; Back: the grid exactly as it was ---------------------
idle(0.3)
sb = place.area.verticalScrollBar()
sb.setValue(sb.maximum() // 2)
app.processEvents()
target = next(i for i, c in enumerate(grid.items()) if c.data["series_id"] == 131)
grid.setCurrentIndex(grid.model_.index(target, 0))
grid.setFocus()
app.processEvents()
before = (w.cat_list.currentRow(), sb.value(), grid.current_item().key, grid.model_.rowCount())
QTest.keyClick(grid, Qt.Key.Key_Return)
settle(lambda: w._series_page_showing() and bool(w._sp_seasons), what="its page")
assert str(w._sp_ctx["series_id"]) == "131" and asked == ["131"], asked
w._on_escape()
app.processEvents()
assert not w._series_page_showing() and w._place_series_showing()
after = (w.cat_list.currentRow(), sb.value(), grid.current_item().key, grid.model_.rowCount())
assert after == before, (before, after)
assert grid.hasFocus() and w.nav_btns["series"].isChecked()
# A click opens it too; the mouse's back button comes back as well.
r = grid.visualRect(grid.model_.index(target, 0))
place.reveal(grid, r)
app.processEvents()
r = grid.visualRect(grid.model_.index(target, 0))
QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
settle(lambda: w._series_page_showing(), what="clicked open")
QTest.mouseClick(w._series_page.view.viewport(), Qt.MouseButton.BackButton)
app.processEvents()
assert w._place_series_showing() and grid.current_item().key == "s:131"
assert asked == ["131"], "opened again within ten minutes: asked again"

# -- Continue: from the series page's cache and from the resume points ----------------
# Show 31 (its episodes cached by the page): S1 watched, S2 E2 half seen.
for eid in (1001, 1002, 1003, 2001):
    w.watched.mark_episode_local_by_stream(eid)
w.resume.record("episode", "2002", 1350, 2700, item={"id": "2002", "season": "2",
                "episode_num": 2, "name": "S2 * E2 - Ep 2", "container_extension": "mkv"},
                series_ctx={"series_id": 131, "name": "Show 31", "category_id": "6"})
# Show 08 (never opened here): its S3 E4, 18 minutes left.
w.resume.record("episode", "7304", 1620, 2700, item={"id": "7304", "season": "3",
                "episode_num": 4, "name": "S3 * E4 - Ep 4", "container_extension": "mkv"},
                series_ctx={"series_id": 108, "name": "Show 08", "category_id": "5"})
w.resume._data["episode:7304"]["ts"] += 10                     # the newer one
n_asked = len(asked)
w._place_series_rows()
settle(lambda: not place.cont.isHidden(), what="Continue")
cards = place.cont.cards()
assert [c.key for c in cards] == ["s:108", "s:131"], [c.key for c in cards]
assert cards[0].line == tr("sp_se", s=3, e=4) + " · " + tr("home_min_left", n=18), cards[0].line
assert abs(cards[0].progress - 0.6) < 0.01
assert cards[1].line.startswith(tr("sp_se", s=2, e=2)) and cards[1].data.known
assert len(asked) == n_asked, "Continue asked the source for series_info"
assert place.popular.isHidden()
# Enter continues S3 E4 where it got to, without the question; the place stays.
asked_resume.clear()
place.cont.strip.setFocus()
place.cont.strip.setCurrentIndex(place.cont.strip.model_.index(0, 0))
QTest.keyClick(place.cont.strip, Qt.Key.Key_Return)
settle(lambda: (w._last_playback or {}).get("key") == "7304", what="S3 E4 plays")
lp = w._last_playback
assert lp["kind"] == "episode" and str(lp["series_ctx"]["series_id"]) == "108", lp
assert asked_resume == [], "Continue asked whether to resume"
assert w._place_series_showing() and not w._series_page_showing()
assert w.series_ctx is None and lp["item"]["name"] == "S3 * E4 - Ep 4"
idle(0.3)
assert place.cont.strip.playing_key == "s:108" and w._place_series_showing()
# Show 31's: the page's own next episode, with its queue for autoplay.
w._place_series_continue(place.cont.cards()[1])
settle(lambda: str((w._last_playback or {}).get("key")) == "2002", what="S2 E2 plays")
assert asked_resume == [] and [e["id"] for e in w._last_playback["ep_queue"]][:2] == ["1001", "1002"]
assert grid.playing_key == "s:131" and w._place_series_showing()

# -- private viewing: Continue plays, nothing of it is kept ------------------------------
w._on_player_stopped()
kept = (json.dumps(w.resume.continue_watching(), sort_keys=True), len(w.history.items()))
privacy.set_private(True)
try:
    w._place_series_continue(place.cont.cards()[0])
    settle(lambda: str((w._last_playback or {}).get("key")) == "7304", what="private play")
    w.player.playback_position = lambda: 2000.0
    w.player.playback_duration = lambda: 2700.0
    w.player.current_url = w.player.current_url or "http://127.0.0.1:9/series/7304.mkv"
    w._on_player_stopped()
    assert (json.dumps(w.resume.continue_watching(), sort_keys=True),
            len(w.history.items())) == kept, "private viewing kept where it got to"
finally:
    privacy.set_private(False)

# -- Popular right now: the chart matched against the source -------------------------
class Tmdb:
    def season_episodes(self, tid, number):
        return {}
    def trending(self, kind, pages, langs):
        return []
class Resolver:
    client = Tmdb()
    def get(self, title, kind, cb=None):
        return None
    def is_resolved(self, title, kind):
        return True
    def tmdb_id_for(self, title, kind):
        return {"Show 05": 5005}.get(title)
    def get_full(self, title, kind, cb=None):
        return {}
    def flush(self):
        pass
w.cover.resolver = Resolver()
w._popular_chart = {"t": time.time(), "lang": "en", "vod": [], "series": [
    {"id": 1, "titles": ["Show 31"], "year": 2019},
    {"id": 2, "titles": ["Late Show"]},
    {"id": 5005, "titles": ["Something Else"]},
    {"id": 3, "titles": ["Show 12"]},
    {"id": 4, "titles": ["Not Here"]}]}
from dopeiptv import i18n
i18n.set_language("en")
listed.clear()
w.cat_list.setCurrentRow(0)                                    # All
settle(lambda: not place.popular.isHidden(), what="Popular")
pop = place.popular.cards()
# In the chart's order, ranked; by TMDB id where it is known; never the
# category locked away - and a kids profile's limits further down.
w.overrides.update("series", "9", locked=True)
w.parental.session_unlocked = False
w._place_series_rows()
pop = place.popular.cards()
assert [c.title for c in pop] == ["Show 31", "Show 05", "Show 12"], [c.title for c in pop]
assert [c.rank for c in pop] == [1, 2, 3] and not place.cont.isHidden()
# In a category: that category's.
w.cat_list.setCurrentRow(row)                                   # Comedy (odd ids)
settle(lambda: place.header.title.text() == "Comedy" and grid.model_.rowCount() == 30, what="Comedy 2")
settle(lambda: [c.title for c in place.popular.cards()] == ["Show 31", "Show 05"], what="Comedy's")
# Opened from there: its page; Back to the place.
place.popular.activated.emit(place.popular.cards()[1])
settle(lambda: w._series_page_showing() and str(w._sp_ctx["series_id"]) == "105", what="popular page")
w._leave_series_page()
assert w._place_series_showing()
# Nothing on the chart this source has: hidden. TMDB off: hidden.
w._popular_chart = {"t": time.time() + 1, "lang": "en", "vod": [],
                    "series": [{"id": 9, "titles": ["Not Here"]}]}
w._place_series_rows()
settle(lambda: place.popular.isHidden(), what="nothing matches")
w._popular_chart["series"] = [{"id": 1, "titles": ["Show 31"]}]
w._popular_chart["t"] = time.time() + 2
w._place_series_rows()
settle(lambda: not place.popular.isHidden(), what="matches again")
settings.setValue("popular_series", "false")                  # Settings > Posters & info
w._place_series_rows()
assert place.popular.isHidden()
settings.setValue("popular_series", "true")
w._place_series_rows()
settle(lambda: not place.popular.isHidden(), what="switched on again")
w.cover.resolver = None
w._place_series_rows()
assert place.popular.isHidden()
# The filter: the list's, and no rows over what it finds.
place.filter.setText("Show 1")
settle(lambda: grid.model_.rowCount() == 5 and w.search.text() == "Show 1", what="filtered")
assert place.cont.isHidden()
place.filter.clear()
settle(lambda: grid.model_.rowCount() == 30, what="unfiltered")
assert not place.cont.isHidden()

# -- a followed series: New episode on its card; the Following category ---------------
w.followed.follow(shows[33])
w.followed.update(133, {"episodes": {"1": season(1, 2)}})
w.followed.update(133, {"episodes": {"1": season(1, 3)}})
grid.grab()
card = next(c for c in grid.items() if c.key == "s:133")
assert card.pills == (("new", tr("pill_new_episode")),), card.pills
w._load_categories()
settle(lambda: any(w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "__followed__"
                   for i in range(w.cat_list.count())), what="Following")
frow = next(i for i in range(w.cat_list.count())
            if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "__followed__")
w.cat_list.setCurrentRow(frow)
settle(lambda: [c.key for c in grid.items()] == ["s:133"], what="the followed")
assert place.header.title.text() == tr("cat_following") and place.cont.isHidden()

# -- the menu of a card: the list's own, for every card selected --------------------
row = next(i for i in range(w.cat_list.count())
           if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "6")
w.cat_list.setCurrentRow(row)
settle(lambda: place.header.title.text() == "Comedy" and grid.model_.rowCount() == 30, what="Comedy 3")
seen = []
orig_exec = QMenu.exec
QMenu.exec = lambda self, *a, **k: seen.append([x.text() for x in self.actions()])
grid.selectionModel().clearSelection()
for i in (0, 1):
    grid.selectionModel().select(grid.model_.index(i, 0),
                                 grid.selectionModel().SelectionFlag.Select)
grid.menu.emit(grid.items()[0], grid.mapToGlobal(grid.rect().center()))
QMenu.exec = orig_exec
texts = seen[0]
assert tr("ctx_add_to_favorites") + " (2)" in texts, texts
assert tr("ctx_follow_series") in texts or tr("ctx_unfollow_series") in texts
assert len(w.listw.selectionModel().selectedRows()) == 2

# -- a kids profile: nothing for adults, here either ---------------------------------
w.overrides.update("series", "9", locked=False)
w.cover.resolver = Resolver()
w._popular_chart = {"t": time.time() + 3, "lang": "en", "vod": [], "series": [
    {"id": 2, "titles": ["Late Show"]}, {"id": 1, "titles": ["Show 31"]}]}
w.resume.record("episode", "9901", 900, 2700, item={"id": "9901", "season": "1",
                "episode_num": 1, "name": "S1 * E1"},
                series_ctx={"series_id": 900, "name": "Late Show", "category_id": "9"})
w.cat_list.setCurrentRow(0)
settle(lambda: [c.title for c in place.popular.cards()] == ["Late Show", "Show 31"], what="adult shows")
assert "s:900" in [c.key for c in place.cont.cards()]
w._profile_kids = lambda: True
w._kids_known()["series"] = (w.client, frozenset({"9"}))
w._load_categories()
settle(lambda: grid.model_.rowCount() == 60, what="kids' All")
settle(lambda: [c.title for c in place.popular.cards()] == ["Show 31"], what="kids' Popular")
assert "s:900" not in [c.key for c in place.cont.cards()]
assert all(c.data["series_id"] != 900 for c in grid.items())
assert not any(w.cat_list.item(i).text().startswith("XXX") for i in range(w.cat_list.count()))
w._profile_kids = lambda: False

# -- a locked category asks for the PIN, as the list did -----------------------------
w.overrides.update("series", "6", locked=True)
w.parental.session_unlocked = False
w._load_categories()
settle(lambda: w.cat_list.count() >= 3, what="locked categories")
pins = []
w._request_unlock = lambda: pins.append(1) or False
lrow = next(i for i in range(w.cat_list.count())
            if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "6")
w.cat_list.setCurrentRow(lrow)
idle(0.3)
assert pins == [1] and all(c.data.get("category_id") != "6" for c in grid.items())
assert all(c.data.series.get("category_id") != "6" for c in place.cont.cards())
print("SERIES_PLACE_OK", flush=True)
os._exit(0)
"""


def test_the_series_place_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path),
               XDG_DATA_HOME=str(tmp_path / "data"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=240)
    assert "SERIES_PLACE_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")
