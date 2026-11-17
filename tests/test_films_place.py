"""The Movies place (core/film_view.py, ui/mw_place_films.py, the detail
column's Continue in ui/mw_detail.py): what a film's card says (year,
length, rating, New, how far, Play or Continue, who is in it); a category
of 20,000 films made at once and dressed only as it comes on screen; and in
the window - Popular right now matched to the source and ranked, Continue,
both hidden when empty or while filtering, Grid / List remembered, the
sort, a click that picks a film for the detail column (Continue there for
one under way) and never plays, a double click or Enter that plays, the
list's menu and several cards at once, a kids profile, a locked category,
private viewing, the pane following a film played from elsewhere - and a
film played from the grid, from Continue, from Popular and from the detail
column, each through the window's own path."""
import os
import subprocess
import sys
import time

import pytest

from dopeiptv.core import film_view as fv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- what a film says (no Qt) ---------------------------------------------------------

def test_year_length_and_rating_from_what_the_source_says():
    assert fv.year_of({"name": "Dune", "year": "2021"}) == "2021"
    assert fv.year_of({"name": "Dune"}, {"releasedate": "2021-10-22"}) == "2021"
    assert fv.year_of({"name": "EN - Dune (2021) 4K"}) == "2021"
    assert fv.year_of({"name": "Dune - 2021"}) == "2021"
    # A number that may be the title's own is no year.
    assert fv.year_of({"name": "Wonder Woman 1984"}) == ""
    assert fv.year_of({"name": "Blade Runner 2049"}) == ""
    assert fv.year_of({"name": "Plain"}) == ""
    assert fv.minutes_of({"name": "x"}, {"duration": "01:52:00"}) == 112
    assert fv.minutes_of({"duration_secs": 5400}) == 90
    assert fv.minutes_of({"name": "x"}, {"runtime": 97}) == 97
    assert fv.minutes_of({"name": "x"}, dur_secs=6000) == 100
    assert fv.minutes_of({"name": "x"}) == 0
    assert fv.rating_of({"rating": "7,4"}) == 7.4
    assert fv.rating_of({"rating": "", "rating_5based": "4"}) == 8.0
    assert fv.rating_of({"rating": "0"}, {"rating": 6.6}) == 6.6
    assert fv.rating_of({}) is None
    assert fv.genres_of({}, {"genre": " Drama,  Action "}) == "Drama, Action"
    assert fv.plot_of({"plot": ""}, {"overview": " a \n b "}) == "a b"


def test_new_watched_and_identity():
    now = 1_800_000_000
    assert fv.is_new({"added": str(now - 3 * 86400)}, now)
    assert not fv.is_new({"added": str(now - 30 * 86400)}, now)
    assert fv.is_new({"added": now + 3600}, now)          # a clock a little ahead
    assert not fv.is_new({"added": now + 10 * 86400}, now)
    assert not fv.is_new({"added": "junk"}, now) and not fv.is_new({}, now)
    assert fv.key_of({"stream_id": 7}) == "7"
    assert fv.key_of({"_url": "http://x/m.mkv"}) == "http://x/m.mkv"
    assert fv.is_movie({"stream_id": 1}) and not fv.is_movie({"stream_id": 1, "stream_type": "live"})
    assert not fv.is_movie({"name": "x"})


def test_the_button_plays_or_continues_by_how_far_it_got():
    p = fv.primary(1800, 7200)
    assert p.resume and p.pos == 1800 and p.progress.fraction == 0.25
    assert p.progress.minutes_left == 90
    assert not fv.primary(30, 7200).resume                 # the first minute
    assert not fv.primary(7000, 7200).resume                # watched to its end
    assert not fv.primary(0, 0).resume
    # No length kept with the point: the film's own.
    assert fv.primary(600, 0, minutes=100).resume


def test_the_cast_from_tmdb_else_the_source_never_asked_for():
    tmdb = {"cast": [{"name": "Ann", "profile_url": "http://p/a.jpg", "person_id": 1},
                     {"name": "ann", "profile_url": "", "person_id": 2},
                     {"name": " Bo  B ", "person_id": 3}, "junk"]}
    assert fv.cast_of({"cast": "X, Y"}, tmdb) == [("Ann", "http://p/a.jpg", 1), ("Bo B", "", 3)]
    assert fv.cast_of({"cast": "X, Y, , X"}, {}) == [("X", "", None), ("Y", "", None)]
    assert fv.cast_of({"actors": "Z"}, None) == [("Z", "", None)]
    assert len(fv.cast_of({"cast": ", ".join(f"P{i}" for i in range(30))}, None)) == 12
    assert fv.cast_of(None, None) == []


# -- the views (Qt, no window) --------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from dopeiptv.ui import theme
    theme.apply_theme(theme="graphite")
    return QApplication.instance() or QApplication([])


def _spin(app, n=8):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def test_twenty_thousand_films_made_at_once_and_dressed_as_they_show(app):
    from dopeiptv.ui import media_cards as mc
    from dopeiptv.ui.mw_place_films import FilmCardView
    dressed = []

    def dress(card):
        dressed.append(card.key)
        card.line = "2021"

    page = mc.PlacePage("Movies")
    view = FilmCardView(None, dress, "poster")
    page.set_content(view)
    t0 = time.perf_counter()
    cards = [mc.Card(key=str(i), title=f"Film {i}", data={"stream_id": i}) for i in range(20000)]
    view.set_items(cards)
    made = time.perf_counter() - t0
    page.resize(900, 700)
    page.show()
    _spin(app)
    page.grab()
    assert made < 1.0, made
    first = len(set(dressed))
    assert 0 < first < 80, first                    # what shows and a few past it
    bar = page.area.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    _spin(app)
    page.grab()
    assert len(set(dressed)) < 200
    assert "10000" not in dressed[:first]
    # Dressed once; again only after a change.
    n = len(dressed)
    page.grab()
    assert len(dressed) == n
    view.redress()
    page.grab()
    assert len(dressed) > n
    # The list layout dresses its rows the same way.
    view.set_layout("list")
    _spin(app)
    page.grab()
    page.close()


# -- in the window ----------------------------------------------------------------------

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.core import privacy
from dopeiptv.i18n import current_language, tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
mw_mod.launch_player = lambda *a, **k: None                  # never a real mpv
w = MainWindow(DemoClient(), settings)
asked_resume = []
w._choice_dialog = lambda *a, **k: asked_resume.append(a) or 0
starts = []
if w.player is not None:
    w.player.play = lambda url, title, start=0.0, **k: starts.append((url, start)) or True


class Resolver:
    # TMDB as the place asks it: nothing fetched, no posters.
    client = None

    def __getattr__(self, name):
        return lambda *a, **k: None

    def is_resolved(self, title, kind):
        return True

    def get_full(self, title, kind, callback):
        return {"cast": [{"name": "Ann Actor", "profile_url": "", "person_id": 7}],
                "genres": "Drama", "overview": ""}


w.cover.resolver = Resolver()


def settle(cond, secs=10, what=""):
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


now = int(time.time())
c = w.client
c.vod_categories = lambda: [{"category_id": "10", "category_name": "Drama"},
                            {"category_id": "11", "category_name": "Action"},
                            {"category_id": "12", "category_name": "Secret"},
                            {"category_id": "13", "category_name": "Big"}]
films = [{"stream_id": 300 + i, "name": f"Film {i:02d}", "year": str(2000 + i),
          "category_id": "11" if i % 2 else "10", "container_extension": "mkv",
          "rating": "7.5", "added": str(now - (2 if i < 4 else 90) * 86400)}
         for i in range(40)]
films.append({"stream_id": 900, "name": "Hidden Hit", "category_id": "12", "container_extension": "mkv"})
films.append({"stream_id": 901, "name": "Adult Film", "category_id": "10", "is_adult": "1",
              "container_extension": "mkv"})
big = [{"stream_id": 10000 + i, "name": f"Big {i:05d}", "category_id": "13",
        "container_extension": "mkv"} for i in range(20000)]
c.vod_streams = lambda cat=None: [f for f in films + big if cat in (None, f["category_id"])]
c.vod_url = lambda sid, ext=None: f"http://127.0.0.1:9/movie/{sid}.{ext or 'mkv'}"
info_asked = []
def vod_info(sid):
    info_asked.append(sid)
    return {"info": {"plot": "The plot.", "genre": "Drama", "duration": "01:40:00",
                     "cast": "Bo Builder, Cy Cast"}}
c.vod_info = vod_info
# This week's chart: two films the source has (one in a hidden category),
# one it has not.
w._popular_chart = {"t": time.time(), "lang": current_language(), "series": [],
                    "vod": [{"id": 1, "titles": ["Hidden Hit"], "year": None},
                            {"id": 2, "titles": ["Film 07"], "year": 2007},
                            {"id": 3, "titles": ["Not Here"], "year": 2020},
                            {"id": 4, "titles": ["Film 02"], "year": 2002}]}
w.overrides.update("vod", "12", hidden=True)
w.watched.mark_movie_local_by_stream(305)
w.resume.record("vod", 303, 1500, 6000, item=films[3])

w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0, what="first list")
idle(1.0)

# -- the place, a category of it ---------------------------------------------------------
assert w._show_place("films") is True                    # the sidebar's Movies
place, view = w._films_place, w._pf_view
assert w.mode == "vod" and w._films_place_showing() and w._place == "films"
assert w.nav_btns["vod"].isChecked()
settle(lambda: w.cat_list.count() >= 4, what="categories")
def cat_row(cid):
    return next(i for i in range(w.cat_list.count())
                if str(w.cat_list.item(i).data(Qt.ItemDataRole.UserRole)) == cid)
w.cat_list.setCurrentRow(cat_row("11"))
settle(lambda: view.model_.rowCount() == 20, what="Action's films")
assert [c.key for c in view.items()] == [str(f["stream_id"]) for f in films if f["category_id"] == "11"]
assert place.header.title_text() == "Action"
assert place.header.count.text() == tr("count_movies", n=20), place.header.count.text()
assert view.model_.rowCount() == w.list_model.rowCount()

# Popular right now: what the source has, in the chart's order, ranked; never a
# hidden category's copy. Continue: the film in progress.
settle(lambda: w._pf_state()["popular"] == ["307", "302"], what="popular matched")
pop, cont = w._pf_popular_row, w._pf_continue_row
assert [c.rank for c in pop.cards()] == [1, 2] and pop.isVisibleTo(place)
assert w._pf_state()["continue"] == ["303"] and cont.isVisibleTo(place)
assert abs(cont.cards()[0].progress - 0.25) < 0.01
assert cont.cards()[0].line == tr("home_min_left", n=75)

# A card dressed as it shows: its line, New, Watched, how far.
place.grab()
c3 = view.item_at(view.row_of("303"))
assert c3.line.startswith("2003") and "★ 7.5" in c3.line, c3.line
assert "new" in c3.pills and abs(c3.progress - 0.25) < 0.01
c5 = view.item_at(view.row_of("305"))
assert "seen" in c5.pills, c5.pills
assert "new" not in view.item_at(view.row_of("311")).pills

# Both rows away while the filter is in use; the filter is the list's own.
place_filter = w._pf_filter
place_filter.setText("Film 1")
settle(lambda: view.model_.rowCount() == 5, what="filtered")   # 11, 13, 15, 17, 19
assert w.search.text() == "Film 1"
assert pop.isHidden() and cont.isHidden()
place_filter.clear()
settle(lambda: view.model_.rowCount() == 20 and not pop.isHidden(), what="unfiltered")
# No TMDB: no Popular; nothing in progress: no Continue.
saved_res = w.cover.resolver
w.cover.resolver = None
w._pf_rows_refresh()
assert pop.isHidden()
w.cover.resolver = saved_res
w.resume.clear("vod", 303)
w._pf_rows_refresh()
assert cont.isHidden()
w.resume.record("vod", 303, 1500, 6000, item=films[3])
w._pf_rows_refresh()
assert not cont.isHidden() and not pop.isHidden()

# -- the sort and Grid / List ----------------------------------------------------------
acts = {a.data(): a for a in place.header._sort_group.actions()}
acts["alpha_desc"].trigger()
settle(lambda: view.item_at(0).key == "339", what="sorted Z-A")
assert settings.value("sort::vod::'11'") == "alpha_desc"
assert place.header.sort_btn.text() == tr("sort_za")
assert [c.key for c in view.items()] == [str(w._item_key(w.list_model.item_at(i)))
                                         for i in range(w.list_model.rowCount())]
acts["global"].trigger()
settle(lambda: view.item_at(0).key == "301", what="sorted back")
assert view.layout_kind == "poster"
place.header.switch.buttons[1].click()
assert view.layout_kind == "list" and settings.value("films_view") == "list"
w.cat_list.setCurrentRow(cat_row("10"))
settle(lambda: view.model_.rowCount() == 21, what="Drama")
assert view.layout_kind == "list"                       # remembered
place.header.switch.buttons[0].click()
assert view.layout_kind == "poster" and settings.value("films_view") == "poster"
assert w._pf_layout() == "poster"

# -- a click picks a film: the detail column shows it, nothing plays --------------------
w.cat_list.setCurrentRow(cat_row("11"))
settle(lambda: view.model_.rowCount() == 20, what="Action again")
place.resize(place.size())
bar = place.area.verticalScrollBar()
settle(lambda: bar.maximum() > 300, what="a page to scroll")
bar.setValue(320)
app.processEvents()
row9 = view.model_.index(9, 0)
QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(row9).center())
app.processEvents()
assert w._films_place_showing()                               # the place stays
assert w.list_model.item_at(w.listw.currentIndex().row())["stream_id"] == 319   # the list follows
assert "Film 19" in w._detail_name, w._detail_name           # the detail column shows it
assert str((getattr(w, "_last_playback", None) or {}).get("key")) != "319"   # a click never plays
assert w.d_continue.isHidden()                                # not under way: no Continue
assert not hasattr(w, "_film_page") and not hasattr(w, "_open_film_page")   # no page of its own

# -- a double click plays it, through the window's path; Enter too ------------------------
QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(row9).center())
app.processEvents()
lp = w._last_playback
assert lp["kind"] == "movie" and str(lp["key"]) == "319", lp
assert asked_resume == []
assert any(str(it.get("_key")) == "319" for it in w.history.items())
idle(0.3)
assert w._films_place_showing()                               # the pane did not move
view.setCurrentIndex(view.model_.index(8, 0))
view.setFocus()
QTest.keyClick(view, Qt.Key.Key_Return)
app.processEvents()
assert str(w._last_playback["key"]) == "317", w._last_playback["key"]

# -- a film under way: Continue in the detail column, from where it stopped ---------------
QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                 pos=view.visualRect(view.model_.index(view.row_of("303"), 0)).center())
app.processEvents()
assert not w.d_continue.isHidden()
assert w.d_continue_btn.text().strip() == tr("card_continue")
assert abs(w.d_continue_bar.value() - 250) <= 2
assert w.d_continue_left.text() == tr("home_min_left", n=75)
w.d_continue_btn.click()
app.processEvents()
assert str(w._last_playback["key"]) == "303" and asked_resume == []
if w.player is not None:
    assert starts[-1][1] == 1500, starts
# Another film picked: its own detail, no Continue.
QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                 pos=view.visualRect(view.model_.index(view.row_of("311"), 0)).center())
app.processEvents()
assert w.d_continue.isHidden()

# -- from Continue: a double click goes on from where it stopped (one click
# only picks while autoplay is off, as in the lists) -----------------------------------
w._pf_play_card(view.item_at(view.row_of("311")))             # something else plays first
before = w._last_playback["key"]
QTest.mouseClick(cont.strip.viewport(), Qt.MouseButton.LeftButton,
                 pos=cont.strip.visualRect(cont.strip.model_.index(0, 0)).center())
app.processEvents()
assert w._last_playback["key"] == before                       # a click picked it
QTest.mouseDClick(cont.strip.viewport(), Qt.MouseButton.LeftButton,
                  pos=cont.strip.visualRect(cont.strip.model_.index(0, 0)).center())
app.processEvents()
assert str(w._last_playback["key"]) == "303" and asked_resume == []
if w.player is not None:
    assert starts[-1][1] == 1500, starts
assert w._films_place_showing()

# -- from Popular: a click shows it in the detail column, a double click plays ------------
pop_at = pop.strip.visualRect(pop.strip.model_.index(0, 0)).center()
QTest.mouseClick(pop.strip.viewport(), Qt.MouseButton.LeftButton, pos=pop_at)
app.processEvents()
assert "Film 07" in w._detail_name and str(w._last_playback["key"]) == "303"
QTest.mouseDClick(pop.strip.viewport(), Qt.MouseButton.LeftButton, pos=pop_at)
app.processEvents()
assert str(w._last_playback["key"]) == "307"

# -- from the grid: the list's own menu, its Play ------------------------------------------
seen = []
orig_exec = QMenu.exec
def fake_exec(menu, *a, **k):
    texts = [x.text() for x in menu.actions()]
    seen.append(texts)
    for act in menu.actions():
        if act.text() == want_action:
            act.trigger()
            break
QMenu.exec = fake_exec
from PyQt6.QtGui import QContextMenuEvent
def menu_at(row):
    at = view.visualRect(view.model_.index(row, 0)).center()
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, at,
                                            view.viewport().mapToGlobal(at)))
    app.processEvents()
want_action = tr("ctx_play_in_mpv")
menu_at(5)
assert str(w._last_playback["key"]) == "311", w._last_playback["key"]
texts = seen[-1]
assert tr("ctx_add_to_favorites") in texts and tr("ctx_watchlist_add") in texts, texts
assert tr("ctx_hide") in texts and tr("ctx_mark_watched") in texts
assert w._films_place_showing() and view.current_item().key == "311"
idle(0.3)
assert w._films_place_showing()
# Several cards at once: Ctrl-click gathers them (nothing plays), the menu
# acts on all of them, as the list's did.
want_action = None
played = w._last_playback["key"]
QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                 view.visualRect(view.model_.index(6, 0)).center())
app.processEvents()
assert w._last_playback["key"] == played
assert len(view.selectionModel().selectedRows()) == 2
assert len(w.listw.selectionModel().selectedRows()) == 2
menu_at(6)
assert any(t.startswith(tr("ctx_add_to_favorites")) and "(2)" in t for t in seen[-1]), seen[-1]
QMenu.exec = orig_exec

# -- the pane follows a film played from elsewhere (the phone's path) ---------------------
w._pb_play_movie(films[8])                                   # Drama's
settle(lambda: w._films_place_showing() and w.cat_list.currentItem().data(Qt.ItemDataRole.UserRole) == "10"
       and view.current_item() is not None and view.current_item().key == "308",
       what="followed to its category")
assert view.playing_key == "308"

# -- a kids profile, a locked category, private viewing ---------------------------------
w._profile_kids = lambda: True
assert not w._pf_allowed(films[-1]) and w._pf_allowed(films[0])   # never the one for adults
w._profile_kids = lambda: False
w.overrides.update("vod", "10", locked=True)
w.parental.session_unlocked = False
asks = []
w._request_unlock = lambda: asks.append(1) or False
assert not w._pf_allowed(films[0]) and asks == [1]
w._request_unlock = lambda: asks.append(2) or True
assert w._pf_allowed(films[0]) and asks == [1, 2]
w._profile_kids = lambda: True
assert not w._pf_allowed(films[0]) and asks == [1, 2]       # never asks a child
w._profile_kids = lambda: False
w.overrides.update("vod", "10", locked=False)
n_hist = len(w.history.items())
privacy.set_private(True)
try:
    w._pf_play_card(view.item_at(view.row_of("306")))
    app.processEvents()
    assert str(w._last_playback["key"]) == "306"
    assert len(w.history.items()) == n_hist                   # nothing kept
finally:
    privacy.set_private(False)

# -- another place and back: as it was --------------------------------------------------
cat_before = w.cat_list.currentItem().data(Qt.ItemDataRole.UserRole)
w._show_place("tv")
settle(lambda: w.mode == "live" and not w._films_place_showing(), what="TV")
w.nav_btns["vod"].click()
settle(lambda: w._films_place_showing() and view.model_.rowCount() > 0, what="Movies again")
assert w.cat_list.currentItem().data(Qt.ItemDataRole.UserRole) == cat_before
# The Guide over it and Esc: back on the place as it was (mw_places).
bar.setValue(min(200, bar.maximum()))
app.processEvents()
held = bar.value()
w._show_place("guide")
settle(lambda: w._guide_page_showing(), what="the guide")
w._on_escape()
settle(lambda: w._films_place_showing(), what="back from the guide")
assert w._place == "films" and bar.value() == held, (w._place, bar.value(), held)
# The places' refresh hook: the rows asked for again.
w._place_films_refresh()
settle(lambda: w._pf_state()["popular"] != [], what="popular again")

# -- 20,000 films in one category --------------------------------------------------------
w.cat_list.setCurrentRow(cat_row("13"))
settle(lambda: view.model_.rowCount() == 20000, secs=30, what="the big category")
t0 = time.perf_counter()
w._pf_list_changed()
took = time.perf_counter() - t0
assert took < 0.5, took
idle(0.3)
bar.setValue(bar.maximum() // 2)
idle(0.3)
place.grab()
assert view.dressed_count() < 400, view.dressed_count()
print("FILMS_PLACE_OK", round(took, 3), flush=True)
os._exit(0)
"""


def test_the_movies_place_and_the_detail_column_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path),
               XDG_DATA_HOME=str(tmp_path / "data"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=300)
    assert "FILMS_PLACE_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")
