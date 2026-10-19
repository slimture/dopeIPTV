"""Home section regressions: the page builds its shelves from the window's
stores, clicking cards leaves Home and acts in the classic view, and the
settings master-toggle hides the nav entry.

Subprocess pattern (see test_multiview): needs a real MainWindow whose
player owns an offscreen QOpenGLWidget.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.mw_home import HomePage

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "home-child")
settings.clear()
w = MainWindow(DemoClient(), settings)
now = time.time()

# Seed the stores the shelves read from.
w.history.add("http://x/live/u/p/1.ts", "Chan One", None, 1, "live")
# Same movie as the resume seed below, under its history identity: Continue
# watching owns partly-watched titles, so Recently viewed must NOT repeat it.
w.history.add("http://x/movie/7.mp4", "Halfway Movie HISTROW", None,
              7, "movie")
w.favs.add("Default", {"stream_id": 5, "name": "Fav Chan",
                       "stream_icon": None})
w.movie_favs.add("Default", {"stream_id": 9, "name": "Fav Movie"})
w.series_favs.add("Default", {"series_id": 12, "name": "Fav Series"})
w.resume.record("vod", 7, pos=600, dur=3600,
                item={"stream_id": 7, "name": "Halfway Movie"})

# Home nav button exists and opens the page. (isHidden, not isVisible:
# the window itself is never shown in this offscreen run.)
assert "home" in w.nav_btns and not w.nav_btns["home"].isHidden()
w._show_home_page()
app.processEvents()
assert w._home_showing()
page = w._home_page
assert isinstance(page, HomePage)

# The synchronous shelves populated from the seeded stores.
texts = []
def walk(widget):
    from PyQt6.QtWidgets import QLabel
    for lbl in widget.findChildren(QLabel):
        texts.append(lbl.text())
walk(page)
joined = " | ".join(texts)
assert "Halfway Movie" in joined, joined     # resume shelf
# ...but its history twin is filtered out of Recently viewed (shelf sync).
assert "Halfway Movie HISTROW" not in joined, joined
assert "Fav Chan" in joined                  # favorites-now shelf (channel)
assert "Fav Movie" in joined                 # favorites shelf (movie fav)
assert "Fav Series" in joined                # favorites shelf (series fav)
assert "Chan One" in joined                  # recently viewed shelf

# Clicking a movie card leaves Home and plays it as a MOVIE (not via the
# mode-sensitive play_item - a movie must not be built as a live URL).
plays = []
def fake_sp(url, title, icon, key, kind, *a, **k):
    plays.append((title, kind))
w._start_playback = fake_sp
w.client.vod_url = lambda sid, ext=None: f"http://x/movie/{sid}.{ext or 'mp4'}"
page._play_media({"stream_id": 7, "name": "Halfway Movie",
                  "container_extension": "mp4"})
app.processEvents()
assert not w._home_showing()
assert plays == [("Halfway Movie", "movie")], plays
# ...and the detail panel under the player shows THIS movie, not whatever the
# classic list had selected before (the stale-channel-info bug). (The exact
# classic mode it lands in depends on which sections the demo provider has
# content for, so that isn't asserted here.)
assert w._detail_name == "Halfway Movie", w._detail_name

# Switching any classic mode also leaves Home.
w._show_home_page(); app.processEvents()
assert w._home_showing()
w.switch_mode("live"); app.processEvents()
assert not w._home_showing()

# A continue-watching EPISODE card must play the episode AND drill the middle
# column into its series' episode list: the drill rides the async category
# load (a plain switch_mode landed on "all series" when that load reset the
# list), and the pending-jump key selects the episode row once the list fills.
played = []
w.play_item = lambda it, *a, **k: played.append(it.get("id"))
ctx = {"series_id": 12, "name": "Fav Series"}
page._play_media({"_kind": "episode", "id": 42, "name": "S1 * E3 - Ep",
                  "container_extension": "mp4", "_series_ctx": ctx})
assert played == [42], played                 # played, before navigation
assert w.mode == "series"
assert w._pending_series_drill == ctx          # drill armed for the cat load
assert w._pending_jump_key == 42, w._pending_jump_key
app.processEvents()

# A Recently-viewed EPISODE row (carrying the stored series snapshot) plays
# as an EPISODE - so the resume prompt finds its saved position - and drills
# into its series. The context-less fallback used to degrade it to a "movie":
# restarted from zero, duplicated in History and posterless.
w.switch_mode("live"); app.processEvents()
hist_plays = []
w._start_playback = (lambda url, title, icon, key, kind, *a, **kw:
                     hist_plays.append((url, kind)))
w._pending_series_drill = None
page._play_history({"_kind": "episode", "_key": 77,
                    "_url": "http://x/series/77.mp4",
                    "name": "Show · S1 * E1 - Pilot",
                    "_series_ctx": {"series_id": 12, "name": "Fav Series"}})
assert hist_plays == [("http://x/series/77.mp4", "episode")], hist_plays
assert w.mode == "series"
assert w._pending_series_drill == {"series_id": 12, "name": "Fav Series"}
assert w._pending_jump_key == 77, w._pending_jump_key
app.processEvents()
# ...while an OLD entry without the snapshot still replays (as a movie).
w.switch_mode("live"); app.processEvents()
hist_plays.clear()
page._play_history({"_kind": "episode", "_key": 78,
                    "_url": "http://x/series/78.mp4", "name": "S01 E02"})
assert hist_plays == [("http://x/series/78.mp4", "movie")], hist_plays

# The Watch Later shelf shows playable movies/shows from the watchlist store
# (rows without a provider id are filtered out - no dead cards).
w.watchlist.movies = [{"stream_id": 9, "name": "WL Movie"},
                      {"name": "Trakt-only Movie"}]
w.watchlist.shows = [{"series_id": 12, "name": "WL Show"}]
w._show_home_page(); app.processEvents()
texts.clear()
walk(w._home_page)
joined2 = " | ".join(texts)
assert "WL Movie" in joined2
assert "WL Show" in joined2
assert "Trakt-only Movie" not in joined2

# Master toggle off: nav hidden, showing refused, page left if open.
w.settings.setValue("home_enabled", "false")
w._apply_home_settings()
assert w.nav_btns["home"].isHidden()
w._show_home_page()
assert not w._home_showing()

print("HOME_OK")
"""


def test_home_page():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    assert "HOME_OK" in proc.stdout, (
        f"home checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")


def test_is_movie_filters_live_channels():
    """The Movies shelf must exclude live channels a provider mixes into the
    VOD list, and keep real movie rows (no stream_type, or 'movie')."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dopeiptv.ui.mw_home import HomePage
    is_movie = HomePage._is_movie
    assert is_movie({"stream_id": 1, "stream_type": "movie"})
    assert is_movie({"stream_id": 2})                      # blank type kept
    assert not is_movie({"stream_id": 3, "stream_type": "live"})
    assert not is_movie({"stream_id": 4, "stream_type": "radio"})
    assert not is_movie({"stream_type": "movie"})          # no id, not a movie


_CARD_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import sip
from PyQt6.QtCore import QEventLoop, QPoint, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from dopeiptv.ui.mw_home import _Card

app = QApplication.instance() or QApplication([])
L = Qt.MouseButton.LeftButton
R = Qt.MouseButton.RightButton

# What a real click does: open something with its own event loop (the
# resume dialog) and leave Home, which deletes the card that was clicked.
card = _Card(160, 240, "Halfway Movie")
card.show()
seen = []
def act():
    seen.append("clicked")
    loop = QEventLoop()
    QTimer.singleShot(0, loop.quit)
    loop.exec()
    card.deleteLater()
card.clicked.connect(act)

QTest.mousePress(card, L, pos=QPoint(20, 20))
assert seen == [], "a card must not act on press"
QTest.mouseRelease(card, L, pos=QPoint(20, 20))
assert seen == [], "a card must not act inside the mouse event"
app.processEvents()
assert seen == ["clicked"], seen
app.processEvents()
app.sendPostedEvents(None, 0)
assert sip.isdeleted(card)

# Released outside the card (dragged off): no click.
c2 = _Card(160, 240, "x"); c2.show()
hits = []
c2.clicked.connect(lambda: hits.append(1))
QTest.mousePress(c2, L, pos=QPoint(10, 10))
QTest.mouseRelease(c2, L, pos=QPoint(900, 900))
app.processEvents()
assert hits == [], hits

# Right button reaches right_clicked, not clicked.
rights = []
c2.right_clicked.connect(lambda: rights.append(1))
QTest.mousePress(c2, R, pos=QPoint(10, 10))
QTest.mouseRelease(c2, R, pos=QPoint(10, 10))
app.processEvents()
assert rights == [1] and hits == [], (rights, hits)

# A card deleted between the release and the deferred emit stays quiet.
c3 = _Card(160, 240, "y"); c3.show()
c3.clicked.connect(lambda: hits.append("late"))
QTest.mouseRelease(c3, L, pos=QPoint(10, 10))
sip.delete(c3)
app.processEvents()
assert hits == [], hits
print("CARD_OK")
"""


def test_a_home_card_acts_after_the_click_not_inside_it():
    """The macOS crash on dismissing the resume prompt with the window's
    close button: the card emitted from inside its press handler, so the
    prompt's event loop and the Home teardown that deletes the card both
    ran while Qt still held the press as a grab on it. The card now acts on
    release, after the event has been delivered."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CARD_CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=120)
    assert "CARD_OK" in proc.stdout, (
        f"card checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")


_POPULAR_CHILD = r"""
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QLabel

from dopeiptv.i18n import current_language, tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.mw_home import _Shelf

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "home-popular")
settings.clear()
w = MainWindow(DemoClient(), settings)
movies = [
    # The copy in a hidden category gives way to the visible one.
    {"stream_id": 3, "name": "AR - Dune: Part Two (2024)",
     "category_id": "2", "added": "7"},
    {"stream_id": 1, "name": "EN - Inception (2010)", "category_id": "1",
     "added": "5"},
    {"stream_id": 2, "name": "EN - Dune: Part Two (2024)",
     "category_id": "1", "added": "6"},
    {"stream_id": 4, "name": "Some Old Film", "category_id": "1",
     "added": "8"},
]
shows = [{"series_id": 20, "name": "Shogun (2024)", "category_id": "9"}]
w.client.vod_streams = lambda category_id=None: movies
w.client.series_list = lambda category_id=None: shows
w.overrides.update("vod", "2", hidden=True)
w._popular_chart = {
    "t": time.time(), "lang": current_language(),
    "vod": [{"id": 693134, "titles": ["Dune: Part Two"], "year": 2024},
            {"id": 1, "titles": ["Not On The Provider"], "year": 2024},
            {"id": 27205, "titles": ["Inception"], "year": 2010}],
    "series": [{"id": 126308, "titles": ["Shōgun"], "year": 2024}]}

def settle(cond, secs=15):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return cond()

def shelves():
    out = {}
    for shelf in w._home_page.findChildren(_Shelf):
        if shelf.isHidden():
            continue                     # torn down by a rebuild
        head = shelf.findChildren(QLabel)[0].text()
        cards = [shelf._h.itemAt(i).widget() for i in range(shelf.count())]
        out[head] = [[l.text() for l in c.findChildren(QLabel)
                      if l.text()][0] for c in cards]
    return out

def same(got, want):
    # Card titles are elided to the card's width.
    return len(got) == len(want) and all(
        w_.startswith(g.rstrip("…")) for g, w_ in zip(got, want))

pop_m, pop_s = tr("home_pop_movies"), tr("home_pop_series")
w._show_home_page()
assert settle(lambda: pop_m in shelves()), shelves()
got = shelves()
assert same(got[pop_m], ["EN - Dune: Part Two (2024)",
                         "EN - Inception (2010)"]), got
assert same(got[pop_s], ["Shogun (2024)"]), got
heads = list(got)
assert heads.index(pop_m) < heads.index(pop_s) \
    < heads.index(tr("home_new_movies")), heads

# A card plays its movie.
plays = []
w._start_playback = (lambda url, title, icon, key, kind, *a, **k:
                     plays.append((title, kind)))
w.client.vod_url = lambda sid, ext=None: f"http://x/movie/{sid}.mp4"
shelf = [s for s in w._home_page.findChildren(_Shelf)
         if not s.isHidden() and s.findChildren(QLabel)[0].text() == pop_m][0]
shelf._h.itemAt(0).widget().clicked.emit()
app.processEvents()
assert plays == [("EN - Dune: Part Two (2024)", "movie")], plays

# Switched off in Settings: gone at once, the other one stays.
w._show_home_page()
assert settle(lambda: pop_m in shelves()), shelves()
settings.setValue("home_sh_pop_movies", "false")
w._apply_home_settings()
assert settle(lambda: pop_m not in shelves() and pop_s in shelves()), \
    shelves()
# Both off: no chart is wanted, so none is fetched.
settings.setValue("home_sh_pop_series", "false")
assert w._home_page._trending() is None
settings.setValue("home_sh_pop_movies", "true")
settings.setValue("home_sh_pop_series", "true")

# A day-old chart is fetched again in the background, kept on disk, and
# the shelves follow it.
class FakeTmdb:
    def trending(self, kind, pages, languages):
        if kind != "vod":
            return []
        return [{"id": 27205, "titles": ["Inception"], "year": 2010}]

class FakeResolver:
    client = FakeTmdb()
    def tmdb_id_for(self, name, kind): return None
    def get(self, *a, **k): return None
    def get_full(self, *a, **k): return None
    def is_resolved(self, *a, **k): return True
    def flush(self): return None

w.cover.resolver = FakeResolver()
w._popular_chart = dict(w._popular_chart, t=time.time() - 2 * 86400)
w._show_home_page()
assert settle(lambda: same(shelves().get(pop_m, []),
                          ["EN - Inception (2010)"])), shelves()
assert pop_s not in shelves()
assert os.path.exists(w._home_page._chart_path())
print("POPULAR_OK")
"""


def test_home_popular_shelves(tmp_path):
    """Popular movies / series right now: TMDB's chart of the week, only
    what the provider has, in the chart's order, above Recently added."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               XDG_CACHE_HOME=str(tmp_path))
    env.pop("DOPEIPTV_TMDB_KEY", None)        # never the real TMDB here
    proc = subprocess.run(
        [sys.executable, "-c", _POPULAR_CHILD], capture_output=True,
        text=True, env=env, cwd=_REPO_ROOT, timeout=180)
    assert "POPULAR_OK" in proc.stdout, (
        f"popular checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}")
