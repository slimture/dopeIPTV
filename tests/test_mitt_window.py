"""Mine in the real window (ui/mw_mitt.py): ``_mitt_show`` puts the page in
the middle pane; its sections hold what the stores hold - of every source,
the favourite channels by this hour's habit, the queue in its own order;
and everything plays through the window's own paths (a fake player takes
the stream): Continue where it got to without the question, History's
Continue and Play again, a channel live (another source's as the Search
page plays it), a station, a series' page and Back to Mine. The heart puts
a favourite back where it was; a title watched leaves the queue; a kids
profile, a locked folder and private viewing behave as the lists do; Esc
empties the search, then goes back. With a 5 000-row History and 300
favourites the window's thread stays quick."""
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
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.core import mitt, privacy
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

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
asked = []
w._choice_dialog = lambda *a, **k: asked.append(a) or 0
w.client.vod_url = lambda sid, ext=None: f"http://pa.invalid/movie/{sid}.{ext or 'mkv'}"
w.client.episode_url = lambda eid, ext=None: f"http://pa.invalid/series/{eid}.{ext or 'mkv'}"
w.client.series_info = lambda sid: {"info": {"name": "Kustlinjen"}, "episodes": {
    "2": [{"id": str(500 + e), "episode_num": e, "season": 2, "title": f"E{e}",
           "container_extension": "mkv"} for e in range(1, 7)]}}

def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out"

def idle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0 or w.mode != "live")
idle(1.0)

plays = []
def fake_play(url, title, start=0.0, **_k):
    plays.append((url, round(float(start))))
    w.player.current_url = url
    return True
w.player.play = fake_play

# -- the stores --------------------------------------------------------------------------
now = datetime.now()
_since_midnight = now - now.replace(hour=0, minute=0, second=0, microsecond=0)
# *ago*, squeezed into today when the test runs just after midnight:
# History's first heading is Today whatever the clock says (CI at 00:02
# put "three hours ago" on yesterday).
def today_ago(ago):
    span = timedelta(hours=3, minutes=5)
    return ago if _since_midnight >= span else ago * (_since_midnight / span)
chans = w.client.live_streams(None)                         # stream ids 1..5
own = w._own(w.settings)
for c in chans[:4]:
    w.favs.add("all", dict(c))
w.favs.add("Barn", dict(chans[3]))
w.favs.add("Vuxen", dict(chans[4]))                          # only in a locked folder
w.favs.set_group_locked("Vuxen", True)
w.radio_favs.add("all", {"stationuuid": "u1", "name": "Radio Klassisk", "url": "http://r.invalid/1"})
w.movie_favs.add("all", {"stream_id": 40, "name": "Favourite Film"})
own.setValue("favorites_pb", json.dumps({"all": [{"stream_id": 77, "name": "Nordic One"}]}))
own.setValue("history_pb", json.dumps([{"name": "Nordic One", "_kind": "live", "_key": 77,
                                        "_url": "http://pb.invalid/77", "stream_id": 77,
                                        "_watched_at": (now - today_ago(timedelta(hours=1))).isoformat()}]))
local = os.path.join(_tmp, "Semester 2025.mkv")
open(local, "wb").write(b"x")
w.resume.record("vod", 30, 1200, 3600, item={"stream_id": 30, "name": "Northern Lights"})
w.resume.record("episode", "504", 600, 2700,
                item={"id": "504", "name": "E4", "season": 2, "episode_num": 4},
                series_ctx={"series_id": 9, "name": "Kustlinjen"})
w.resume.record("local", local, 300, 1800)
def h(name, kind, key, ago, **kw):
    return {"name": name, "_kind": kind, "_key": key, "_url": f"http://pa.invalid/{kind}/{key}",
            "_watched_at": (now - ago).isoformat(), **kw}
w.history.entries = [
    h("Big Buck Bunny", "live", 1, today_ago(timedelta(minutes=10)), stream_id=1),
    h("Northern Lights", "movie", 30, today_ago(timedelta(hours=3))),
    h("Stjärnfall", "movie", 31, timedelta(days=1, hours=1)),
    h("Adults Only", "movie", 32, timedelta(days=2), is_adult="1"),
    h("Kustlinjen · S2 E4", "episode", "504", timedelta(days=3),
      _series_ctx={"series_id": 9, "name": "Kustlinjen"}),
]
w.history._save()
w.followed.follow({"series_id": 9, "name": "Kustlinjen"})
w.followed.series["9"].update(primed=True, new=[{"id": "506", "season": "2", "episode_num": "6",
                                                 "added": int(time.time())}])
w.followed._save()
for sid, name in ((41, "Queue Film A"), (42, "Queue Film B"), (43, "Queue Film C")):
    w.watchlist.add_movie_local({"stream_id": sid, "name": name}, None)
w.watchlist.add_show_local({"series_id": 9, "name": "Kustlinjen"}, None)
w._info_cache[("vod", 41)] = {"duration_secs": 5400}
vlog = w._stats_store()
vlog.titles["live:pa:3"] = {"n": "Sintel", "k": "live",
                            "h": {(now - timedelta(days=7 * k)).strftime("%Y%m%d%H"): 2400
                                  for k in (1, 2, 3)}}

# -- shown ---------------------------------------------------------------------------------
w._show_place("tv")
assert w._mitt_show() is True
page = w._mitt_page
assert w._mitt_showing() and w._place == "mitt"
assert w.nav_btns["mitt"].isChecked()
settle(lambda: w._mitt_data is not None and page.sections["live"].isVisibleTo(page))
idle(0.3)

def items(name):
    return page.section_items(name)

def keyed(name, pred):
    for it in items(name):
        e = getattr(it, "data", None)
        if isinstance(e, mitt.Entry) and pred(e):
            return it
    raise AssertionError(f"not in {name}: {[getattr(i, 'title', '') for i in items(name)]}")

order = [getattr(s, "objectName", lambda: "")() for s in ()]
visible = [n for n in ("continue", "live", "favorites", "queue", "new", "history")
           if page.sections[n].isVisibleTo(page)]
assert visible == ["continue", "live", "favorites", "queue", "new", "history"], visible
# Continue: the film, the episode under its series, the local file.
cont = {(c.data.kind, c.data.ident) for c in items("continue")}
assert {("film", "30"), ("episode", "504"), ("local", local)} <= cont, cont
assert keyed("continue", lambda e: e.kind == "episode").title == "Kustlinjen"
# The favourite channels: Sintel first (Monday-evening habit, three weeks),
# the locked folder's channel not there, another source's and the station there.
tiles = [t.data for t in items("live")]
assert tiles[0].ident == "3" and tiles[0].key in page.tiles.habit, [t.title for t in tiles]
assert page.heads["live"].note.text() == tr("mitt_live_note")
assert "5" not in {t.ident for t in tiles if t.source == "pa"}
assert any(t.source == "pb" and t.ident == "77" for t in tiles)
assert any(t.kind == "radio" for t in tiles)
# The queue: in the order added, its count and time.
q = [c.title for c in items("queue")]
assert q == ["Queue Film A", "Queue Film B", "Queue Film C", "Kustlinjen"], q
assert page.heads["queue"].note.text().startswith(tr("mitt_n_films", n=3)), \
    page.heads["queue"].note.text()
assert tr("mitt_at_least", time=tr("mitt_hours_min", h=1, m="30")) in \
    page.heads["queue"].note.text()
assert keyed("new", lambda e: e.ident == "9").pills == ("new",)
# History as a timeline: today's first, the other source's said so.
hist = items("history")
assert hist[0].title == tr("rec_today")
nordic = keyed("history", lambda e: e.source == "pb")
assert tr("sa_from_source", source="Nordic") in nordic.line

# -- the filter and the search --------------------------------------------------------------
page.chips.set_current("film")
w._mitt_render()
assert not page.sections["live"].isVisibleTo(page)
assert {c.data.kind for c in items("continue")} == {"film"}
assert {c.data.kind for c in items("queue")} == {"film"}
page.chips.set_current("all")
page.field.setText("kust")
w._mitt_render()
assert [c.title for c in items("queue")] == ["Kustlinjen"]
assert page.sections["new"].isVisibleTo(page)
assert not page.sections["live"].isVisibleTo(page)
page.field.setText("zzzz")
w._mitt_render()
assert page.empty.isVisibleTo(page) and page.empty.text() == tr("mitt_nothing")
page.field.setText("")
w._mitt_render()

# -- playing from each section ------------------------------------------------------------
def act(section, item):
    page.activated.emit(section, item)
    app.processEvents()

act("continue", keyed("continue", lambda e: e.kind == "film"))
lp = w._last_playback
assert lp["kind"] == "movie" and str(lp["key"]) == "30" and plays[-1][1] == 1200, (lp, plays)
assert asked == [], "Continue asked whether to resume"
act("continue", keyed("continue", lambda e: e.kind == "episode"))
lp = w._last_playback
assert lp["kind"] == "episode" and str(lp["key"]) == "504" and plays[-1][1] == 600
assert str(lp["series_ctx"]["series_id"]) == "9"
assert w._mitt_showing(), "the page did not stay"
act("continue", keyed("continue", lambda e: e.kind == "local"))
assert w._last_playback["kind"] == "local" and plays[-1] == (local, 300)
act("live", keyed("live", lambda e: e.ident == "3"))
assert w._last_playback["kind"] == "live" and str(w._last_playback["key"]) == "3"
assert w._mitt_showing()
# What plays is marked on the page.
idle(1.3)
assert page.tiles.playing_key == keyed("live", lambda e: e.ident == "3").key
elsewhere = []
w._play_channel_elsewhere = lambda pl, row: elsewhere.append((pl["id"], row["stream_id"]))
act("live", keyed("live", lambda e: e.source == "pb"))
assert elsewhere == [("pb", 77)]
act("live", keyed("live", lambda e: e.kind == "radio"))
assert w._last_playback["kind"] == "radio"
act("favorites", keyed("favorites", lambda e: e.ident == "40"))
assert w._last_playback["kind"] == "movie" and str(w._last_playback["key"]) == "40"
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
qv = page.queue
r = qv.visualRect(qv.model_.index(0, 0))
QTest.mouseClick(qv.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
app.processEvents()
assert str(w._last_playback["key"]) == "40"     # autoplay off (the default): one click picks
QTest.mouseDClick(qv.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
app.processEvents()
assert str(w._last_playback["key"]) == "41" and w._last_playback["kind"] == "movie"
# Autoplay on (Settings > Playback): one click plays, as in the other lists.
w.settings.setValue("autoplay_preview", "true")
assert qv._one_click() and page.timeline._one_click() and page.tiles._one_click()
w.settings.setValue("autoplay_preview", "false")
assert not page.rows["continue"].strip._one_click() and page.rows["new"].strip._one_click()
# History: a channel live, a film with a resume point continues, one
# without plays again from the start - neither asks. (Playing on from the
# film above kept where the fake player got to: nowhere - its point again.)
act("history", keyed("history", lambda e: e.kind == "channel" and e.source == "pa"))
w.resume.record("vod", 30, 1200, 3600, item={"stream_id": 30, "name": "Northern Lights"})
gen = w._mitt_gen
w._mitt_build()
settle(lambda: w._mitt_gen == gen + 1 and w._mitt_data is not None
       and any(c.data.ident == "30" and c.data.pos > 0 for c in items("history")
               if getattr(c, "data", None) is not None))
assert w._last_playback["kind"] == "live"
row = keyed("history", lambda e: e.ident == "30")
assert w._mitt_row_words(row)[1] == tr("card_continue")
act("history", row)
assert plays[-1][1] == 1200
row = keyed("history", lambda e: e.ident == "31")
assert w._mitt_row_words(row)[1] == tr("mitt_play_again")
act("history", row)
assert str(w._last_playback["key"]) == "31" and plays[-1][1] == 0 and asked == []
# A series: its page, and Back comes to Mine.
act("queue", keyed("queue", lambda e: e.kind == "series"))
assert w._series_page_showing() and w.nav_btns["mitt"].isChecked()
settle(lambda: bool(w._sp_seasons))
w._leave_series_page()
assert w._mitt_showing() and w._place == "mitt"
act("new", keyed("new", lambda e: e.ident == "9"))
assert w._series_page_showing()
w._on_escape()
assert w._mitt_showing()
settle(lambda: w._mitt_data is not None)

# -- the heart: out, and back where it was ------------------------------------------------
before = {g: [x["stream_id"] for x in v] for g, v in w.favs.groups.items()}
tile = keyed("live", lambda e: e.ident == "1")
page.heart.emit(tile)
assert not w.favs.is_favorite(1) and tile.key in page.tiles.off
page.heart.emit(tile)
assert w.favs.is_favorite(1) and tile.key not in page.tiles.off
assert {g: [x["stream_id"] for x in v] for g, v in w.favs.groups.items()} == before

# -- the queue: moved, kept; a title watched leaves it --------------------------------------
w._mitt_queue_nudge(keyed("queue", lambda e: e.ident == "43"), -1)
assert [c.title for c in items("queue")][:3] == ["Queue Film A", "Queue Film C", "Queue Film B"]
assert mitt.parse_order(own.value(mitt.ORDER_KEY))[:3] == ["vod:41", "vod:43", "vod:42"]
w._mitt_queue_move(keyed("queue", lambda e: e.kind == "series"), 0)
assert items("queue")[0].title == "Kustlinjen"
w._mark_movie_watched({"stream_id": 42, "name": "Queue Film B"}, False)
assert not w.watchlist.has_movie(None, 42)
assert "Queue Film B" not in [c.title for c in items("queue")]
w._mitt_build()
settle(lambda: "Queue Film B" not in [c.title for c in items("queue")])
assert [c.title for c in items("queue")] == ["Kustlinjen", "Queue Film A", "Queue Film C"]

# -- a locked folder, opened ----------------------------------------------------------------
w.parental.session_unlocked = True
w._mitt_render()                                     # made for the lock shut: read again
settle(lambda: any(t.data.ident == "5" for t in items("live")))
w.parental.session_unlocked = False
w._mitt_render()
settle(lambda: not any(t.data.ident == "5" and t.data.source == "pa" for t in items("live")))

# -- the folders tab ------------------------------------------------------------------------
page.set_tab("folders")
w._mitt_render()
names = [c.title for c in page.folder_tiles.items()]
assert names[0] == tr("nav_favorites") and "Barn" in names
assert any(n.startswith("Vuxen") and tr("cat_locked_suffix") in n for n in names), names
barn = next(c for c in page.folder_tiles.items() if c.title == "Barn")
w._mitt_folder_picked(barn)
cards = page.folder_items.items()
assert [c.title for c in cards] == ["Art of Motion"]
act("folder", cards[0])
assert w._last_playback["kind"] == "live" and str(w._last_playback["key"]) == "4"
# Dropped on Favorites: out of the folder.
w._mitt_folder_drop(cards, w._mitt_folder_target(page.folder_tiles.items()[0]))
assert "Barn" not in w.favs.groups or not w.favs.groups["Barn"]
page.set_tab("overview")
w._mitt_render()

# -- private viewing ------------------------------------------------------------------------
n_hist = len(w.history.entries)
privacy.set_private(True)
try:
    page.field.setText("secret")
    act("live", keyed("live", lambda e: e.ident == "2"))
    assert len(w.history.entries) == n_hist
    w._show_place("tv")
    assert page.query() == ""
finally:
    privacy.set_private(False)

# -- Esc: the search emptied, then back where Mine was come to from -------------------------
w._show_place("mitt")
assert w._mitt_showing()
page.field.setText("abc")
w._on_escape()
assert page.query() == "" and w._mitt_showing()
w._on_escape()
assert not w._mitt_showing() and w._place == "tv"

# -- a kids profile: the source in use only, nothing for adults ------------------------------
w._profile_kids = lambda: True
w._show_place("mitt")
settle(lambda: w._mitt_data is not None and w._mitt_data.made_for[1])
titles = [c.title for c in items("history")]
assert "Adults Only" not in titles
assert not any(getattr(c.data, "source", "") == "pb" for c in items("history")
               if hasattr(c, "data") and c.data is not None)
assert not any(t.data.source == "pb" for t in items("live"))
w._profile_kids = lambda: False

# -- big: 5 000 History rows and 300 favourites ---------------------------------------------
w.history.entries = [h(f"Title {i}", "movie" if i % 3 else "live", 1000 + i,
                       timedelta(minutes=13 * i), stream_id=1000 + i) for i in range(5000)]
w.favs.groups["all"] = [{"stream_id": 2000 + i, "name": f"Channel {i}"} for i in range(300)]
w._show_place("tv")
t0 = time.perf_counter()
w._show_place("mitt")
gui = time.perf_counter() - t0
settle(lambda: w._mitt_data is not None and len(w._mitt_data.history) >= 5000, 20)
assert len(items("history")) <= mitt.HISTORY_PAGE + 20
assert len(items("live")) == mitt.LIVE_SHOWN
t1 = time.perf_counter()
page.field.setText("title 4999")
w._mitt_render()
render = time.perf_counter() - t1
assert [c.title for c in items("history") if not c.key.startswith("head:")] == ["Title 4999"]
print(f"MITT_TIMES gui={gui*1000:.0f}ms build={w._mitt_built_at*1000:.0f}ms "
      f"filter={render*1000:.0f}ms", flush=True)
assert gui < 1.0 and render < 0.5, (gui, render)
print("MITT_WINDOW_OK", flush=True)
os._exit(0)
"""


def test_mine_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=300)
    assert "MITT_WINDOW_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-5000:]!r}")
