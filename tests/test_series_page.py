"""The series page (core/series_view.py, ui/series_page.py,
ui/mw_series_page.py): seasons in order with Specials last, the episode
the primary button plays, the season it opens on and the one kept; the
cards' parts mirrored in a right-to-left language; a season of 150
episodes shown at once, its pictures fetched on the loader's threads and
only for the cards on screen; and in the window - opened over the list,
a season switched in place, Continue, an episode played and marked with
the page kept (autoplay's next followed), Back to the list exactly as it
was, private viewing, a kids profile and a locked category."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from dopeiptv.core import series_view as sv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ep(eid, num, season=None, title="", plot="", secs=0, still=""):
    ep = {"id": str(eid), "episode_num": num, "title": title,
          "info": {"plot": plot, "duration_secs": secs, "movie_image": still}}
    if season is not None:
        ep["season"] = season
    return ep


# -- what the page shows (no Qt) ------------------------------------------------------

def test_seasons_in_order_with_specials_last():
    eps = {"2": [_ep(21, 2), _ep(20, 1)], "0": [_ep(1, 1)], "1": [_ep(10, 1)],
           "10": [_ep(100, 1)], "x": [None, "junk"]}
    got = sv.seasons(eps)
    assert [s.number for s in got] == [1, 2, 10, 0]
    assert got[-1].specials
    assert [e["id"] for e in got[1].episodes] == ["20", "21"]     # by number
    flat = [_ep(5, 1, season=3), _ep(6, 1, season="S01"), _ep(7, 2)]
    assert [s.number for s in sv.seasons(flat)] == [1, 3, None]
    assert sv.seasons(None) == [] and sv.seasons({}) == []
    # Watching order: the Specials only on their own (or when they are all).
    assert [e["id"] for e in sv.flow(got)] == ["10", "20", "21", "100"]
    assert [e["id"] for e in sv.flow(got, specials=True)] == ["1"]
    assert [e["id"] for e in sv.flow(sv.seasons({"0": [_ep(1, 1)]}))] == ["1"]


def _next(order, watched=(), going=None):
    going = going or {}
    return sv.next_up(order, lambda e: e["id"] in watched,
                      lambda e: going.get(e["id"], sv.Progress()))


def test_continue_picks_the_episode_to_watch_next():
    order = [_ep(i, i) for i in range(1, 7)]
    # Nothing watched: Play the first.
    n = _next(order)
    assert n.episode["id"] == "1" and n.first and not n.resume
    # Watched up to 3: the 4th, as Continue.
    n = _next(order, watched={"1", "2", "3"})
    assert n.episode["id"] == "4" and not n.first and not n.resume
    # One in progress after the last watched: that one, with how far.
    n = _next(order, watched={"1", "2"},
              going={"3": sv.Progress(600, 2400, 50)})
    assert n.episode["id"] == "3" and n.resume and n.progress.fraction == 0.25
    assert n.progress.minutes_left == 30
    # Of two in progress past the last watched, the newest.
    n = _next(order, watched={"1"}, going={"3": sv.Progress(600, 2400, 50),
                                           "5": sv.Progress(900, 2400, 90)})
    assert n.episode["id"] == "5"
    # One left behind (watched past it since): the one after the last watched.
    n = _next(order, watched={"1", "2", "3", "4"},
              going={"2": sv.Progress(600, 2400, 99)})
    assert n.episode["id"] == "5" and not n.resume
    # Everything watched but one earlier in progress: that one.
    n = _next(order, watched={"1", "2", "4", "5", "6"},
              going={"3": sv.Progress(600, 2400, 9)})
    assert n.episode["id"] == "3" and n.resume
    # Everything watched: Play the first again.
    n = _next(order, watched={e["id"] for e in order})
    assert n.episode["id"] == "1" and n.first
    # A position in the first minute or near the end is not "in progress".
    assert not sv.Progress(30, 2400).started and not sv.Progress(2350, 2400).started
    assert _next([]).episode is None


def test_the_season_it_opens_on_and_the_one_kept():
    seasons = sv.seasons({"1": [_ep(11, 1), _ep(12, 2)], "2": [_ep(21, 1)],
                          "3": [_ep(31, 1)]})
    nxt = sv.NextUp(seasons[1].episodes[0], first=False)
    # The next episode's season.
    assert sv.opening_season(seasons, nxt, None) == 1
    # A season picked while that episode was next: kept while it still is.
    raw = sv.remember("", "p:9", 3, "21")
    assert sv.remembered(raw, "p:9") == (3, "21")
    assert sv.opening_season(seasons, nxt, sv.remembered(raw, "p:9")) == 2
    # Watched on since: the next episode's season again.
    later = sv.NextUp(seasons[0].episodes[1], first=False)
    assert sv.opening_season(seasons, later, sv.remembered(raw, "p:9")) == 0
    # An episode asked for wins (what plays, a new one).
    assert sv.opening_season(seasons, nxt, None, focus=seasons[2].episodes[0]) == 2
    # Nothing at all: the first season that is not Specials.
    sp = sv.seasons({"0": [_ep(1, 1)], "1": [_ep(11, 1)]})
    assert sv.opening_season(sp, sv.NextUp(None), None) == 0
    assert sp[sv.opening_season(sp, sv.NextUp(None), None)].number == 1
    # Kept per series, the newest last, capped.
    raw = ""
    for i in range(10):
        raw = sv.remember(raw, f"s{i}", 1, "", cap=4)
    assert list(json.loads(raw)) == ["s6", "s7", "s8", "s9"]
    assert sv.remembered("not json", "s1") is None
    assert sv.remembered(raw, "nope") is None


def test_the_words_on_a_card():
    ep = _ep(1, 5, title="Breaking Bad - S02E05 - Breakage")
    assert sv.episode_title(ep, "Breaking Bad") == "Breakage"
    assert sv.episode_title(_ep(1, 3, title="Show S01E03"), "Show") == ""
    assert sv.episode_title(_ep(1, 3, title="3"), "") == ""
    assert sv.episode_title(_ep(1, 2, title="Cat's in the Bag..."), "") == "Cat's in the Bag..."
    assert sv.minutes_of(_ep(1, 1, secs=2700)) == 45
    for text, mins in (("00:45:10", 45), ("01:02:00", 62), ("45:10", 45),
                       ("45 min", 45), ("2700", 45), ("", 0)):
        assert sv.minutes_of({"info": {"duration": text}}) == mins, text
    assert sv.year_of("", None, "2008-01-20") == "2008"
    assert sv.rating_of("", "8,6", 7) == 8.6 and sv.rating_of("x", 0) is None
    assert sv.backdrop_of([], ["", "https://a/b.jpg"]) == "https://a/b.jpg"
    assert sv.backdrop_of("ftp://x", None) == ""
    assert sv.still_of(_ep(1, 1, still="http://s/1.jpg")) == "http://s/1.jpg"
    assert sv.still_of({"info": {}, "_sp_still": "/thumbs/1.jpg"}) == "/thumbs/1.jpg"
    assert sv.still_of({"info": {"movie_image": "javascript:x"}}) == ""
    assert sv.plot_of({"info": {"plot": "  a\n b "}}) == "a b"
    assert sv.episode_key({"id": 7}) == "7" and sv.episode_key({"_key": "/a.mkv"}) == "/a.mkv"


# -- the page itself (Qt, no window) --------------------------------------------------

@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from dopeiptv.ui import theme
    theme.apply_theme(theme="graphite")
    return QApplication.instance() or QApplication([])


def test_a_card_is_mirrored_in_a_right_to_left_language(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.series_cards import card_layout
    row = QRect(0, 0, 600, 123)
    ltr, rtl = card_layout(row, False), card_layout(row, True)
    assert ltr["still"].left() < ltr["text"].left()
    assert rtl["still"].left() > rtl["text"].left()
    assert rtl["still"].right() == row.right() - (ltr["still"].left() - row.left())
    assert ltr["still"].width() == rtl["still"].width() == 176
    assert abs(ltr["still"].height() - 99) <= 1


def test_the_page_is_mirrored_in_a_right_to_left_language(app):
    from PyQt6.QtCore import Qt
    from dopeiptv.ui.series_page import SeriesPage
    seasons = sv.seasons({str(n): [_ep(n * 10 + i, i) for i in range(1, 4)]
                          for n in (1, 2, 3)})
    for rtl in (False, True):
        page = SeriesPage()
        if rtl:
            page.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        page.resize(700, 800)
        page.header.set_text("Show", "2020", "A plot.")
        page.set_seasons(seasons, [("S1", "Season 1"), ("S2", "Season 2"),
                                   ("S3", "Season 3")], 0)
        page.show()
        app.processEvents()
        chips = page.season_bar.chips
        x = [c.mapTo(page, c.rect().topLeft()).x() for c in chips]
        poster = page.header.poster.mapTo(page, page.header.poster.rect().topLeft()).x()
        title = page.header.title.mapTo(page, page.header.title.rect().topLeft()).x()
        back = page.back_btn.mapTo(page, page.back_btn.rect().topLeft()).x()
        if rtl:
            assert x[0] > x[1] > x[2], x
            assert poster > title and back > page.width() // 2
        else:
            assert x[0] < x[1] < x[2], x
            assert poster < title and back < page.width() // 2
        page.close()
        page.deleteLater()


class _Response:
    status_code = 200

    def __init__(self, data):
        self.content = data

    def raise_for_status(self):
        pass


def _png() -> bytes:
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QImage
    img = QImage(32, 18, QImage.Format.Format_RGB32)
    img.fill(QColor("#336699"))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def test_a_season_of_150_builds_at_once_and_its_pictures_come_on_threads(app, monkeypatch):
    from PyQt6.QtCore import QThreadPool
    from dopeiptv.core import workers
    from dopeiptv.ui.series_page import EpisodeState, SeriesPage
    png = _png()
    fetched: list[tuple[str, bool]] = []

    def fake_get(url, **_kw):
        fetched.append((url, threading.current_thread() is threading.main_thread()))
        time.sleep(0.01)
        return _Response(png)

    monkeypatch.setattr(workers, "_image_get", lambda _u: fake_get)
    pool = QThreadPool()
    pool.setMaxThreadCount(4)
    loader = workers.LogoLoader(pool, max_size=320, cache_dir=None, disk_budget=0)
    eps = [_ep(1000 + i, i, season=1, title=f"Episode number {i}",
               plot="Something happens and then something else happens " * 3,
               secs=2700, still=f"http://img.example/{i}.png")
           for i in range(1, 151)]
    for ep in eps:
        ep["_sp_label"] = f"E{ep['episode_num']} · {ep['title']}"
    seasons = sv.seasons(eps + [_ep(9, 1, season=2)])
    page = SeriesPage(loader)
    page.resize(720, 860)
    page.show()
    app.processEvents()
    t0 = time.perf_counter()
    page.set_seasons(seasons, [("S1", ""), ("S2", "")], 0)
    page.set_states({sv.episode_key(e): EpisodeState(None, sv.Progress(900, 2700, 1))
                     for e in eps[:5]}, sv.episode_key(eps[5]), "")
    app.processEvents()
    page.grab()                                   # the cards on screen painted
    took = time.perf_counter() - t0
    assert page.model.rowCount() == 150
    assert took < 1.0, f"a season of 150 took {took:.3f} s"
    # Switching seasons: the page stays, the list is swapped in place.
    t0 = time.perf_counter()
    page.pick_season(1)
    page.pick_season(0)
    app.processEvents()
    assert time.perf_counter() - t0 < 0.3
    assert page.current == 0 and page.model.rowCount() == 150
    end = time.time() + 5
    while time.time() < end and (pool.activeThreadCount() or not fetched):
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    # Asked for on the loader's threads, never the window's - and only the
    # cards on screen, not all 150.
    assert fetched, "no picture was asked for"
    assert not any(on_main for _u, on_main in fetched), fetched
    assert len({u for u, _m in fetched}) < 40, len(fetched)
    # Scrolled to the end: the last cards' pictures are asked for then.
    page.area.verticalScrollBar().setValue(page.area.verticalScrollBar().maximum())
    app.processEvents()
    page.grab()
    end = time.time() + 5
    while time.time() < end and not any(u.endswith("/150.png") for u, _m in fetched):
        app.processEvents()
        time.sleep(0.02)
    assert any(u.endswith("/150.png") for u, _m in fetched)
    pool.waitForDone(3000)
    page.close()
    page.deleteLater()


# -- scrolling stays cheap (the page was slower to scroll than any list) --------------

class _MemoryLoader:
    """A loader with its pictures in memory already (or none): what the
    page asks for is noted, nothing is fetched."""

    def __init__(self, pictures=None):
        self.cache = dict(pictures or {})
        self.asked: list[str] = []

    def is_dead(self, _url):
        return False

    def get(self, url, _callback):
        self.asked.append(url)


def _still_url(i):
    return f"http://img.example/s/{i}.jpg"


def _picture(w, h, colour):
    from PyQt6.QtGui import QColor, QPixmap
    pm = QPixmap(w, h)
    pm.fill(QColor(colour))
    return pm


def _season(n, stills=True):
    eps = [_ep(7000 + i, i, season=1, title=f"Episode number {i}",
               plot="Something happens and then something else happens " * 3,
               secs=2700, still=_still_url(i) if stills else "")
           for i in range(1, n + 1)]
    for ep in eps:
        ep["_sp_label"] = f"E{ep['episode_num']} · {ep['title']}"
        ep["_sp_short"] = f"E{ep['episode_num']}"
    return eps


def _page_in_the_pane(app, loader, eps, size=(900, 800)):
    """The page as the window holds it: in the middle pane, the app's
    style sheet on (it is what makes a scroll area's content see-through)."""
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from dopeiptv.ui import theme
    from dopeiptv.ui.series_page import SeriesPage
    app.setStyleSheet(theme.build_style())
    host = QWidget(objectName="MiddlePane")
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    page = SeriesPage(loader)
    lay.addWidget(page)
    host.resize(*size)
    host.show()
    app.processEvents()
    page.header.set_text("Show", "2020", "A plot. " * 20)
    backdrop = _picture(1280, 720, "#335577")
    page.header.set_backdrop(backdrop)
    page.set_fallback_art(backdrop)
    page.set_seasons(sv.seasons(eps), [("S1", "")], 0)
    app.processEvents()
    return host, page


def _flush(app, host):
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication
    app.processEvents()
    QApplication.sendEvent(host, QEvent(QEvent.Type.UpdateRequest))
    app.processEvents()


def test_a_scroll_step_paints_only_the_strip_that_comes_into_view(app, monkeypatch):
    """The page's body is opaque, so a scroll step moves what is drawn and
    paints only the strip that comes into view - a card or two. Over the
    see-through body every step painted every card on screen again (eight
    to nine of them), and nothing is laid out again per step."""
    from dopeiptv.ui.series_cards import EpisodeDelegate
    eps = _season(150)
    loader = _MemoryLoader({_still_url(i): _picture(320, 180, "#446688")
                            for i in range(1, 151)})
    paints, hints = [0], [0]
    real_paint, real_hint = EpisodeDelegate.paint, EpisodeDelegate.sizeHint

    def paint(self, p, option, index):
        paints[0] += 1
        real_paint(self, p, option, index)

    def size_hint(self, option, index):
        hints[0] += 1
        return real_hint(self, option, index)

    monkeypatch.setattr(EpisodeDelegate, "paint", paint)
    monkeypatch.setattr(EpisodeDelegate, "sizeHint", size_hint)
    host, page = _page_in_the_pane(app, loader, eps)
    try:
        sb = page.area.verticalScrollBar()
        start = page.view.mapTo(page.body, page.view.rect().topLeft()).y()
        sb.setValue(start)
        _flush(app, host)
        assert paints[0] >= 5, "the cards on screen were not painted"
        per_step = []
        hints[0] = 0
        for v in range(start + 60, start + 60 * 31, 60):
            paints[0] = 0
            sb.setValue(v)
            _flush(app, host)
            per_step.append(paints[0])
        assert max(per_step) <= 3, per_step
        assert sum(per_step) <= len(per_step) * 2, per_step
        assert hints[0] == 0, f"laid out again while scrolling ({hints[0]} size hints)"
    finally:
        host.close()
        host.deleteLater()
        app.setStyleSheet("")


class _SlowLoader(_MemoryLoader):
    """Pictures come in when the test says (the loader's threads, held)."""

    def __init__(self):
        super().__init__()
        self.waiting: list = []

    def get(self, url, callback):
        super().get(url, callback)
        self.waiting.append((url, callback))

    def deliver(self, count):
        for url, callback in self.waiting[:count]:
            n = int(url.rsplit("/", 1)[1].split(".")[0])
            pm = _picture(320, 180, f"#{(n * 40) % 256:02x}{(n * 90) % 256:02x}80")
            self.cache[url] = pm
            callback(pm)
        del self.waiting[:count]


def test_what_is_on_screen_is_what_the_page_paints_while_pictures_come_in(app):
    """Scrolled step by step while pictures come in between the steps: what
    the window shows (moved pixels, the strips painted, the cards painted
    again for their picture) is a fresh paint of the page - no card
    left with its stand-in, no strip out of place."""
    from PyQt6.QtGui import QImage
    eps = _season(60)
    loader = _SlowLoader()
    host, page = _page_in_the_pane(app, loader, eps, size=(820, 700))
    try:
        sb = page.area.verticalScrollBar()

        def on_screen() -> QImage:
            return host.screen().grabWindow(host.winId()).toImage().convertToFormat(
                QImage.Format.Format_RGB32)

        def painted() -> QImage:
            return host.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)

        def same(a: QImage, b: QImage) -> bool:
            # A gradient painted in strips can land a level off at a seam;
            # a card left with its stand-in is far off.
            if a == b:
                return True
            pa = a.constBits().asstring(a.sizeInBytes())
            pb = b.constBits().asstring(b.sizeInBytes())
            return not any(abs(x - y) > 2 for x, y in zip(pa, pb, strict=True))

        _flush(app, host)
        steps = compared = 0
        for v in list(range(0, 2400, 45)) + list(range(2400, 0, -70)):
            sb.setValue(v)
            _flush(app, host)
            steps += 1
            if steps % 3 == 0:
                loader.deliver(4)                # a few in, between two steps
                _flush(app, host)
            if steps % 9 == 0:
                shown, fresh = on_screen(), painted()
                if shown.size() == fresh.size():
                    assert same(shown, fresh), f"stale on screen at {v}"
                    compared += 1
        loader.deliver(len(loader.waiting))
        _flush(app, host)
        shown, fresh = on_screen(), painted()
        if shown.size() == fresh.size():
            assert same(shown, fresh), "stale on screen at the end"
            compared += 1
        assert compared, "what is on screen could not be read"
    finally:
        host.close()
        host.deleteLater()
        app.setStyleSheet("")


def test_cards_and_header_scale_their_pictures_once(app, monkeypatch):
    """A card's still (or the series' picture standing in) and the header's
    backdrop are scaled to their size once, then painted as they are - not
    smooth-scaled again on every paint."""
    from dopeiptv.ui import series_cards, series_page
    scaled: list[tuple] = []
    real = series_cards._cover

    def counting(pm, w, h):
        scaled.append((pm.cacheKey(), w, h))
        return real(pm, w, h)

    monkeypatch.setattr(series_cards, "_cover", counting)
    monkeypatch.setattr(series_page, "_cover", counting)
    eps = _season(40)
    # Every third card without a picture of its own: the series' stands in.
    loader = _MemoryLoader({_still_url(i): _picture(320, 180, "#446688")
                            for i in range(1, 41) if i % 3})
    host, page = _page_in_the_pane(app, loader, eps)
    try:
        sb = page.area.verticalScrollBar()
        page.grab()
        for v in range(0, sb.maximum() + 1, 90):
            sb.setValue(v)
            _flush(app, host)
        first = list(scaled)
        assert first, "nothing was painted"
        assert len(first) == len(set(first)), "a picture was scaled twice"
        for v in range(sb.maximum(), -1, -90):
            sb.setValue(v)
            _flush(app, host)
        page.grab()
        host.grab()
        assert scaled == first, f"{len(scaled) - len(first)} scales on later paints"
        assert len(page.delegate.covers) <= len(set(first))
        # Another series: the scaled copies go with it.
        page.reset()
        assert len(page.delegate.covers) == 0
    finally:
        host.close()
        host.deleteLater()
        app.setStyleSheet("")


def test_pictures_asked_for_on_screen_and_a_few_past_it(app):
    """Only the cards on screen and AHEAD past either edge ask for their
    pictures - before they scroll on - and a picture that comes in paints
    again only the cards that show it."""
    from PyQt6.QtCore import QPoint
    eps = _season(150)
    loader = _MemoryLoader()
    host, page = _page_in_the_pane(app, loader, eps)
    try:
        area, view = page.area, page.view
        sb = area.verticalScrollBar()
        index = page.model.index
        near: set[int] = set()
        for v in (0, 700, 5000, 9000, sb.maximum()):
            sb.setValue(v)
            _flush(app, host)
            at = view.viewport().mapTo(area.viewport(), QPoint(0, 0))
            seen = {r for r in range(150)
                    if view.visualRect(index(r, 0)).translated(at).intersects(
                        area.viewport().rect())}
            rows = set(page._rows_on_screen())
            assert rows == seen, (v, sorted(rows), sorted(seen))
            ahead = set(page._rows_on_screen(page.AHEAD))
            assert seen <= ahead and len(ahead) <= len(seen) + 2 * page.AHEAD
            # The ones about to scroll on are asked for before they show.
            assert {_still_url(r + 1) for r in ahead} <= set(loader.asked), v
            near |= set(page._rows_on_screen(page.AHEAD + 1))
        # Nothing far from where the page was asked for (a row of slack for
        # the first ask, made before the page's layout settled), nothing twice.
        asked_rows = {int(u.rsplit("/", 1)[1].split(".")[0]) - 1 for u in loader.asked}
        assert asked_rows <= near, sorted(asked_rows - near)
        assert len(loader.asked) == len(set(loader.asked)), "a picture asked for twice"
        # One picture in: only its card painted again.
        sb.setValue(5000)
        _flush(app, host)
        row = next(iter(page._rows_on_screen()))
        updated = []
        lv = view.viewport()
        real_update = lv.update
        lv.update = lambda *a: updated.append(a)
        try:
            page._still_arrived(_still_url(row + 1))
            page._still_arrived("http://img.example/nowhere.jpg")
        finally:
            lv.update = real_update
        assert updated == [(view.visualRect(index(row, 0)),)], updated
    finally:
        host.close()
        host.deleteLater()
        app.setStyleSheet("")


# -- in the window -------------------------------------------------------------------

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.core import privacy, series_view as sv
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
launched = []
mw_mod.launch_player = lambda *a, **k: launched.append(a)   # never a real mpv
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None                                       # no TMDB here
w._choice_dialog = lambda *a, **k: 0                          # no modal question

def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out"

cats = [{"category_id": "5", "category_name": "Drama"},
        {"category_id": "6", "category_name": "Comedy"}]
shows = [{"series_id": 100 + i, "name": f"Show {i:02d}", "category_id": "6", "cover": ""}
         for i in range(60)]
w.client.series_categories = lambda: cats
w.client.series_list = lambda cat=None: [s for s in shows if cat in (None, s["category_id"])]

def season(n, count):
    return [{"id": str(n * 1000 + e), "episode_num": e, "title": f"Ep {e}",
             "container_extension": "mkv",
             "info": {"plot": "A plot.", "duration_secs": 2700}} for e in range(1, count + 1)]

info = {"info": {"name": "Show 30", "plot": "The series.", "genre": "Comedy",
                 "releaseDate": "2019-04-01", "rating": "8.2"},
        "episodes": {"1": season(1, 3), "2": season(2, 4), "0": season(0, 1)}}
asked = []
def series_info(sid):
    asked.append(sid)
    return info
w.client.series_info = series_info
w.client.episode_url = lambda i, ext=None: f"http://x/series/u/p/{i}.{ext or 'mkv'}"

def idle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

w.resize(1440, 900)
w.show()
# The window's own start (its first list, the guide) over before the test's.
settle(lambda: w.list_model.rowCount() > 0)
idle(1.5)

# A watched first season, S2 E2 half seen: Continue S2 E2.
for eid in (1001, 1002, 1003, 2001):
    w.watched.mark_episode_local_by_stream(eid)
w.resume.record("episode", "2002", 1350, 2700, item={"id": "2002"},
                series_ctx={"series_id": 130, "name": "Show 30"})

# -- the list, somewhere in it ------------------------------------------------------
w.switch_mode("series")
settle(lambda: w.cat_list.count() >= 2)
row = next(i for i in range(w.cat_list.count())
           if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "6")
w.cat_list.setCurrentRow(row)
settle(lambda: w.list_model.rowCount() == 60)
idle(0.5)
assert w.list_model.rowCount() == 60
w.listw.setCurrentIndex(w.list_model.index(30))
w.listw.verticalScrollBar().setValue(w.listw.verticalScrollBar().maximum() // 2)
app.processEvents()
before = (w.cat_list.currentRow(), w.listw.verticalScrollBar().value(),
          w.listw.currentIndex().row(), w.list_model.rowCount(), w.mode)

# -- opened from the list --------------------------------------------------------------
w.play_item(w.list_model.item_at(30))
page = w._series_page
assert w._series_page_showing(), "the page did not open"
settle(lambda: bool(w._sp_seasons))
assert [s.number for s in w._sp_seasons] == [1, 2, 0]
assert page.season_bar.isVisibleTo(page) and len(page.season_bar.chips) == 3
assert page.season_bar.chips[2].text() == tr("sp_specials")
assert page.current == 1, page.current                       # the next one's season
assert page.next_key == "2002", page.next_key
assert page.header.play_btn.text().strip() == tr(
    "sp_continue", episode=tr("sp_se", s=2, e=2)), page.header.play_btn.text()
assert page.header.progress.isVisibleTo(page.header)
assert abs(page.header.progress.value() - 500) <= 2
assert page.states["1001"].watched and not page.states["2003"].watched
assert page.header.title.text() == "Show 30" and "2019" in page.header.meta.text()
assert page.view.currentIndex().row() == 1                   # on S2 E2

# -- a season switched: the page stays --------------------------------------------------
page.season_bar.chips[0].click()
app.processEvents()
assert w._series_page_showing() and page.current == 0
assert [e["id"] for e in page.shown_episodes()] == ["1001", "1002", "1003"]
page.view.setFocus()
QTest.keyClick(page.view, Qt.Key.Key_Right)
assert page.current == 1 and w._series_page_showing()
QTest.keyClick(page.view, Qt.Key.Key_Left)
assert page.current == 0
# Kept for the next time, with the episode next now.
assert sv.remembered(settings.value("series_seasons"), w._sp_series_key()) == (1, "2002")

# -- Continue plays S2 E2 from where it got to, without the question; the
#    page stays and marks it ---------------------------------------------------------
asked_resume = []
w._choice_dialog = lambda *a, **k: asked_resume.append(a) or 0
page.header.play_btn.click()
app.processEvents()
lp = w._last_playback
assert lp["kind"] == "episode" and str(lp["key"]) == "2002", lp
assert str(lp["series_ctx"]["series_id"]) == "130"
assert asked_resume == [], "Continue asked whether to resume"
assert w._series_page_showing() and page.current == 1
assert page.playing_key == "2002", page.playing_key
assert w.series_ctx is None                                  # only for the moment
assert [e["id"] for e in lp["ep_queue"]] == ["1001", "1002", "1003", "2001",
                                              "2002", "2003", "2004"]
assert lp["ep_index"] == 4
# Clicking what plays starts nothing over.
n = len(w.history.items()) if hasattr(w.history, "items") else 0
page.play_requested.emit(page.shown_episodes()[1])
assert str(w._last_playback["key"]) == "2002"

# -- an episode clicked once: picked, nothing plays ("Auto-play preview on selection"
# is off, as in the lists); double-clicked: it plays, the page stays; autoplay's next is
# followed ---------
w._choice_dialog = lambda *a, **k: 0
page.season_bar.chips[0].click()
app.processEvents()
r = page.view.visualRect(page.model.index(2, 0))
QTest.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
app.processEvents()
assert str(w._last_playback["key"]) == "2002", w._last_playback["key"]
assert page.view.currentIndex().row() == 2
QTest.mouseDClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
app.processEvents()
assert str(w._last_playback["key"]) == "1003" and w._series_page_showing()
assert page.playing_key == "1003" and page.current == 0
w.playback_mode = lambda: "embedded"
assert w._advance_to_next_episode()
app.processEvents()
assert str(w._last_playback["key"]) == "2001", w._last_playback["key"]
assert page.playing_key == "2001" and page.current == 1, (page.playing_key, page.current)
assert page.view.currentIndex().row() == 0
# With "Auto-play preview on selection" on, one click plays.
settings.setValue("autoplay_preview", "true")
r = page.view.visualRect(page.model.index(1, 0))
QTest.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
app.processEvents()
assert str(w._last_playback["key"]) == "2002", w._last_playback["key"]
settings.setValue("autoplay_preview", "false")
r = page.view.visualRect(page.model.index(0, 0))
QTest.mouseDClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
app.processEvents()
assert str(w._last_playback["key"]) == "2001", w._last_playback["key"]

# -- the episode's menu: what the list offered ------------------------------------------
from PyQt6.QtWidgets import QMenu
seen = []
orig_exec = QMenu.exec
QMenu.exec = lambda self, *a, **k: seen.append([x.text() for x in self.actions()])
w._sp_episode_menu(page.shown_episodes()[2], page.mapToGlobal(page.rect().center()))
QMenu.exec = orig_exec
texts = seen[0]
assert tr("ctx_play_in_mpv") in texts and tr("ctx_mark_watched") in texts, texts
assert tr("ctx_continue_remove") not in texts
assert tr("ctx_copy_stream_url") in texts and tr("ctx_follow_series") in texts

# A mark from it lands on the card at once.
w._sp_with_series(w._mark_episode_watched, page.shown_episodes()[2], False)
assert page.states["2003"].watched

# -- Back: the list exactly as it was ----------------------------------------------------
w._on_escape()
app.processEvents()
assert not w._series_page_showing()
after = (w.cat_list.currentRow(), w.listw.verticalScrollBar().value(),
         w.listw.currentIndex().row(), w.list_model.rowCount(), w.mode)
assert after == before, (before, after)
assert w.nav_btns["series"].isChecked()

# Again (its episodes kept a few minutes: not asked for again), and back by
# the mouse's back button, then by the arrow.
n_asked = len(asked)
w.play_item(w.list_model.item_at(30))
assert w._series_page_showing()
settle(lambda: bool(w._sp_seasons) and w._series_page.model.rowCount())
assert len(asked) == n_asked, "a series opened again was asked for again"
QTest.mouseClick(page.view.viewport(), Qt.MouseButton.BackButton)
app.processEvents()
assert not w._series_page_showing()
w.play_item(w.list_model.item_at(30))
page.back_btn.click()
app.processEvents()
assert not w._series_page_showing()
assert (w.cat_list.currentRow(), w.listw.currentIndex().row()) == (before[0], before[2])

# A category clicked while it shows: that list, in its place.
w.play_item(w.list_model.item_at(30))
assert w._series_page_showing()
w._sp_category_clicked(w.cat_list.item(row))
assert not w._series_page_showing()

# -- private viewing keeps no season -------------------------------------------------
settings.remove("series_seasons")
w.play_item(w.list_model.item_at(30))
settle(lambda: w._series_page.model.rowCount() > 0)
privacy.set_private(True)
try:
    page.pick_season(2)
    assert settings.value("series_seasons") in (None, ""), settings.value("series_seasons")
finally:
    privacy.set_private(False)
page.pick_season(0)
assert settings.value("series_seasons")
w._leave_series_page()

# -- a kids profile never opens a series for adults; a locked category asks ---------
w._profile_kids = lambda: True
w._open_series_page({"series_id": 900, "name": "Adult", "is_adult": "1"})
assert not w._series_page_showing()
w._profile_kids = lambda: False
w.overrides.update("series", "6", locked=True)
w.parental.session_unlocked = False
asks = []
w._request_unlock = lambda: asks.append(1) or False
w._open_series_page({"series_id": 130, "name": "Show 30", "category_id": "6"})
assert asks == [1] and not w._series_page_showing()
w._request_unlock = lambda: asks.append(2) or True
w._open_series_page({"series_id": 130, "name": "Show 30", "category_id": "6"})
assert asks == [1, 2] and w._series_page_showing()
w._profile_kids = lambda: True
w._leave_series_page()
w._open_series_page({"series_id": 130, "name": "Show 30", "category_id": "6"})
assert not w._series_page_showing()                         # never asks a child
w._profile_kids = lambda: False
w.overrides.update("series", "6", locked=False)

# -- the sidebar's logo on a playing episode: its page, the episode marked -------------
w._jump_to_now_playing()
settle(lambda: w._series_page_showing() and w._series_page.playing_key == "2001")
assert w._series_page.current == 1

# -- a local library's series: the same page, its files played as local ------------
w._leave_series_page()
lib = tempfile.mkdtemp()
files = []
for se, ep in ((1, 1), (1, 2), (2, 1)):
    path = os.path.join(lib, f"Show.Name.S{se:02d}E{ep:02d}.mkv")
    open(path, "wb").write(b"x")
    files.append((se, ep, path, os.path.splitext(os.path.basename(path))[0]))
w._local_series_index = {"Show Name": files}
w._open_local_series_page("Show Name", {"name": "Show Name", "_kind": "localseries"})
assert w._series_page_showing()
page = w._series_page
assert [s.number for s in w._sp_seasons] == [1, 2] and page.model.rowCount() == 2
assert page.header.fav_btn.isHidden() and page.header.title.text() == "Show Name"
assert page.header.play_btn.text().strip() == tr("sp_play", episode=tr("sp_se", s=1, e=1))
page.header.play_btn.click()
app.processEvents()
assert w._last_playback["kind"] == "local" and w._last_playback["key"] == files[0][2]
assert [r["_path"] for r in w._track_queue] == [f[2] for f in files]   # plays on
assert page.playing_key == files[0][2]
w._leave_series_page()
assert not w._series_page_showing()

# -- the command palette finds the series and opens its page ----------------------------
w._leave_series_page()
w._palette_load_catalogue()
settle(lambda: getattr(w, "_pal_catalogue", None) is not None
       and len(w._pal_catalogue.series) == 60)
found = w._palette_find_channels("show 30", ["show", "30"])
rows = [e for e in found if e.payload[0] == "series"]
assert rows and rows[0].text == "Show 30", [e.text for e in found]
w._palette_then("series", rows[0].payload[1])
settle(lambda: w._series_page_showing())
print("SERIES_PAGE_OK", flush=True)
os._exit(0)
"""


def test_the_series_page_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=240)
    assert "SERIES_PAGE_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")


# -- regressions found in review ------------------------------------------------------

_CHILD_REVIEW = r"""
import os, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod, mw_series_page
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
launched = []
mw_mod.launch_player = lambda *a, **k: launched.append(a)
mw_series_page.launch_player = lambda *a, **k: launched.append(a)
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
asked = []
w._choice_dialog = lambda *a, **k: asked.append(a) or 0

def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out"

def season(n, count):
    return [{"id": str(n * 1000 + e), "episode_num": e, "title": f"Ep {e}",
             "container_extension": "mkv",
             "info": {"plot": "A plot.", "duration_secs": 2700}} for e in range(1, count + 1)]

info = {"info": {"name": "Show 30"}, "episodes": {"1": season(1, 3), "2": season(2, 2)}}
w.client.series_info = lambda sid: info
w.client.episode_url = lambda i, ext=None: f"http://x/series/u/p/{i}.mkv"
w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0)
series = {"series_id": 130, "name": "Show 30", "category_id": "6"}

def episode(eid):
    return next(e for s in w._sp_seasons for e in s.episodes if e["id"] == eid)

# -- a followed series' new episodes are new once: opened again within the
#    ten minutes its episodes are kept, none of them is still "new" -----------
w.followed.follow(series)
w.followed.update(130, {"episodes": {"1": season(1, 2)}})
w.followed.update(130, {"episodes": {"1": season(1, 3), "2": season(2, 2)}})
assert set(w.followed.new_ids(130)) == {"1003", "2001", "2002"}
w._open_series_page(series)
settle(lambda: bool(w._sp_seasons))
page = w._series_page
assert page.states["1003"].new and page.states["2002"].new
w._leave_series_page()
w._open_series_page(series)
settle(lambda: bool(w._sp_seasons))
stale = [k for k, s in w._series_page.states.items() if s.new]
assert stale == [], f"seen episodes still marked new: {stale}"

# -- a live channel whose id is an episode's (ids are unique only within a
#    type) does not stop that episode from playing ---------------------------
w._start_playback("http://x/live/u/p/2002.ts", "Channel", None, 2002, "live")
if not getattr(w.player, "current_url", None):
    w.player.current_url = "http://x/live/u/p/2002.ts"
assert w._playing_group == "live" and str(w._playing_key) == "2002"
w._sp_play(episode("2002"))
assert w._last_playback["kind"] == "episode" and str(w._last_playback["key"]) == "2002", \
    w._last_playback

# -- an episode the recordings' question turned back leaves no autoplay
#    queue behind for the next episode played from elsewhere ---------------------
guard = w._guard_stream_switch
w._guard_stream_switch = lambda url, title: False
w._sp_play(episode("1001"))
w._guard_stream_switch = guard
assert str(w._last_playback["key"]) == "2002"
assert w._ep_queue_override is None, w._ep_queue_override

# -- a portal (its addresses slow to get): Open externally asks for the
#    address off the window's thread, and Continue still starts where it got
#    to without the question --------------------------------------------------
ready, blocked = set(), []
def slow_url(i, ext=None):
    on_main = threading.current_thread() is threading.main_thread()
    if on_main and str(i) not in ready:
        blocked.append(str(i))
    if not on_main:
        time.sleep(0.05)
        ready.add(str(i))
    return f"http://x/series/u/p/{i}.mkv"
w.client.episode_url = slow_url
w.client.slow_links = True
w.client.link_ready = lambda name, ident: str(ident) in ready
w._choice_dialog = lambda *a, **k: asked.append(a) or 1      # open anyway
launched.clear()
w._sp_play_external(episode("1002"), "mpv")
settle(lambda: launched)
assert blocked == [], f"the address was asked for on the window's thread: {blocked}"
assert launched[-1][1].endswith("/1002.mkv"), launched

w._sp_play(episode("1001"))
settle(lambda: str(w._last_playback["key"]) == "1001")
w.resume.record("episode", "2001", 1350, 2700, item={"id": "2001"},
                series_ctx={"series_id": 130, "name": "Show 30"})
w._sp_refresh()
nxt = w._sp_next
assert nxt.episode["id"] == "2001" and nxt.resume, (nxt.episode, nxt.resume)
asked.clear()
w._choice_dialog = lambda *a, **k: asked.append(a) or 0
w._sp_primary()
settle(lambda: str(w._last_playback["key"]) == "2001")
assert asked == [], f"Continue asked whether to resume: {asked}"
assert blocked == [], blocked
print("SERIES_REVIEW_OK", flush=True)
os._exit(0)
"""


def test_series_page_review_regressions_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", _CHILD_REVIEW], capture_output=True,
                          text=True, env=env, cwd=_REPO_ROOT, timeout=240)
    assert "SERIES_REVIEW_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")


def test_the_header_buttons_read_over_a_backdrop_on_a_light_theme(app):
    """Favorite, Follow, Watch later and ⋯ stand out from the backdrop washed
    toward a light page - a dark, a mid and a light one - and from the plain
    surface: their usual fill was nearly the colour under them."""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QColor, QPixmap
    from dopeiptv.ui import theme
    from dopeiptv.ui.series_page import SeriesHeader
    light = [k for k, t in theme.THEMES.items() if t.get("light")]
    assert len(light) >= 3
    try:
        for key in light:
            theme.apply_theme(theme=key)
            app.setStyleSheet(theme.build_style())
            for colour in (QColor(20, 20, 30), QColor(90, 70, 50), QColor(220, 200, 180), None):
                h = SeriesHeader()
                h.resize(760, 240)
                h.set_text("Show", "2020", "A plot.")
                h.set_actions(True)
                h.set_primary("Play S1 E1", None, "")
                if colour is not None:
                    backdrop = QPixmap(640, 360)
                    backdrop.fill(colour)
                    h.set_backdrop(backdrop)
                h.show()
                app.processEvents()
                img = h.grab().toImage()
                for b in (h.fav_btn, h.later_btn, h.more_btn):
                    inside = b.mapTo(h, QPoint(4, b.height() // 2))
                    beside = b.mapTo(h, QPoint(-3, b.height() // 2))
                    fill = img.pixelColor(inside).name()
                    ground = img.pixelColor(beside).name()
                    ratio = theme.contrast(fill, ground)
                    assert ratio >= 1.2, (key, colour and colour.name(), b.text() or "more",
                                          fill, ground, ratio)
                h.close()
                h.deleteLater()
    finally:
        theme.apply_theme(theme="graphite")
        app.setStyleSheet("")


_CHILD_REVIEW2 = r"""
import os, tempfile, time
from types import SimpleNamespace
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod, mw_cast, mw_series_page
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
mw_mod.launch_player = lambda *a, **k: None
mw_series_page.launch_player = lambda *a, **k: None


class FakeDialog:
    def __init__(self, *a, **k):
        pass

    def exec(self):
        return 0


mw_cast.CastDialog = FakeDialog
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
w._choice_dialog = lambda *a, **k: 0

def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out"

def season(n, count):
    return [{"id": str(n * 1000 + e), "episode_num": e, "title": f"Ep {e}",
             "container_extension": "mkv",
             "info": {"plot": "A plot.", "duration_secs": 2700}} for e in range(1, count + 1)]

info = {"info": {"name": "Show 30"}, "episodes": {"1": season(1, 3), "2": season(2, 2)}}
w.client.series_info = lambda sid: info
w.client.episode_url = lambda i, ext=None: f"http://x/series/u/p/{i}.mkv"
w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0)
series = {"series_id": 130, "name": "Show 30", "category_id": "6"}
w._open_series_page(series)
settle(lambda: bool(w._sp_seasons))
page = w._series_page

def episode(eid):
    return next(e for s in w._sp_seasons for e in s.episodes if e["id"] == eid)

# -- a cast from the page: History keeps the episode's series, also when the
#    receiver says it plays after the dialog closed (and on every strip
#    refresh after) -----------------------------------------------------------------
ep = episode("1002")
w._sp_with_series(w._open_cast_dialog, ep)
assert w.series_ctx is None
w.show_cast_strip("Living room TV", ep["name"])
w.show_cast_strip("Living room TV", ep["name"])
row = next(r for r in w.history.items() if str(r.get("_key")) == "1002")
assert row["_kind"] == "episode" and (row.get("_series_ctx") or {}).get("series_id") == 130, row
w.show_cast_strip(None)

# -- an episode replayed from History: played with its series (marks, resume,
#    scrobbles), marked on its page, no stray autoplay queue -------------------------
hist = {"_url": "http://x/series/u/p/2001.mkv", "_key": "2001", "_kind": "episode",
        "name": "Show 30 · S2 * E1 - Ep 1",
        "_series_ctx": {"series_id": 130, "name": "Show 30"}}
w._ensure_home_page()._play_history(hist)
app.processEvents()
lp = w._last_playback
assert str(lp["key"]) == "2001" and (lp.get("series_ctx") or {}).get("series_id") == 130, lp
assert lp["ep_queue"] == [], [e.get("name") for e in lp["ep_queue"]][:3]
assert w.series_ctx is None
settle(lambda: w._series_page_showing() and w._series_page.playing_key == "2001")

# -- TV mode and watch together: an episode turned back leaves no queue -------------
guard = w._guard_stream_switch
w._guard_stream_switch = lambda url, title: False
w._tv_play_episode(episode("1001"), series, [episode("1001"), episode("1002")])
assert w._ep_queue_override is None, "TV mode left its queue"
w._together_room = SimpleNamespace(clock=SimpleNamespace(now_ms=lambda: 0))
found = SimpleNamespace(kind="episode", row=episode("1003"), url="http://x/series/u/p/1003.mkv",
                        series=series, queue=[episode("1003")])
state = SimpleNamespace(position_at=lambda now: 0.0, playing=False,
                        media=SimpleNamespace(name="Ep 3"))
w._together_play(found, state)
assert w._ep_queue_override is None, "watch together left its queue"
w._together_room = None
w._guard_stream_switch = guard

# -- episodes as a list of lists (PHP: seasons 0..n) and a flat list -----------------
w._leave_series_page()
w.client.series_info = lambda sid: {"episodes": [season(0, 1), season(1, 2)]}
w._open_series_page({"series_id": 140, "name": "Show 40"})
settle(lambda: bool(w._sp_seasons))
assert [s.number for s in w._sp_seasons] == [1, 0], [s.number for s in w._sp_seasons]
w._leave_series_page()
flat = season(1, 2) + season(2, 1)
for e in flat:
    e["season"] = int(e["id"]) // 1000
w.client.series_info = lambda sid: {"episodes": flat}
w._open_series_page({"series_id": 141, "name": "Show 41"})
settle(lambda: bool(w._sp_seasons))
assert [s.number for s in w._sp_seasons] == [1, 2]
assert [len(s.episodes) for s in w._sp_seasons] == [2, 1]
w._leave_series_page()

# -- whatever the answer: the page never stays loading ------------------------------
rows = w._episode_rows
w._episode_rows = lambda info, series: 1 / 0
w._open_series_page({"series_id": 150, "name": "Show 50"})
settle(lambda: not w._series_page.loading.isVisibleTo(w._series_page))
assert w._series_page.retry.isVisibleTo(w._series_page), "no way to try again"
w._episode_rows = rows
w._leave_series_page()

# -- a title TMDB matched before backdrops were kept: its details asked for
#    again by its id, once, and the backdrop shown ---------------------------------
asked = []

class Resolver:
    client = SimpleNamespace(season_episodes=lambda tid, n: {})

    def __getattr__(self, name):            # the list's covers: none here
        return lambda *a, **k: None

    def is_resolved(self, title, kind):
        return True

    def get_full(self, title, kind, callback):
        return {"tmdb_id": 55, "poster_url": None}

    def tmdb_id_for(self, title, kind):
        return 55

    def fill_backdrop(self, title, kind, callback):
        asked.append(title)
        callback({"tmdb_id": 55, "poster_url": None,
                  "backdrop_url": "https://img.invalid/w780/b.jpg"})
        return True

w.cover.resolver = Resolver()
w.client.series_info = lambda sid: info
w._open_series_page({"series_id": 160, "name": "Show 60"})
settle(lambda: bool(w._sp_seasons))
assert asked == ["Show 60"], asked
assert w._sp_head["backdrop"] == "https://img.invalid/w780/b.jpg", w._sp_head
w._sp_refresh()
w._sp_fill_header(None)
assert asked == ["Show 60"], asked
w.cover.resolver = None
print("SERIES_REVIEW2_OK", flush=True)
os._exit(0)
"""


def test_series_page_review_regressions_more(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", _CHILD_REVIEW2], capture_output=True,
                          text=True, env=env, cwd=_REPO_ROOT, timeout=240)
    assert "SERIES_REVIEW2_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")


def _old_episode_rows(info, series):
    """MainWindow._episode_rows as it was (a dict of seasons only): the
    dict form must come out exactly the same."""
    from dopeiptv.i18n import tr
    series_cover = (series.get("cover") or series.get("cover_big")
                    or series.get("stream_icon") or "")
    series_title = series.get("name") or series.get("title") or ""
    episodes = []
    for season, eps in ((info or {}).get("episodes") or {}).items():
        for ep in eps:
            ep["season"] = season
            ep["name"] = (f"S{season} * E{ep.get('episode_num', '?')} - "
                          f"{ep.get('title') or tr('misc_episode')}")
            still = (ep.get("info") or {}).get("movie_image") or ""
            ep["stream_icon"] = ep.get("stream_icon") or still or series_cover
            ep["_series_title"] = series_title
            episodes.append(ep)
    return episodes


def test_episode_rows_take_a_dict_a_flat_list_or_a_list_of_lists(app):
    import copy
    from dopeiptv.ui.main_window import MainWindow
    series = {"name": "Show", "cover": "http://c/p.jpg"}
    shaped = {"episodes": {"1": [_ep(11, 1, title="One", still="http://s/1.jpg"), _ep(12, 2)],
                           "0": [_ep(1, 1, title="")]}}
    old = _old_episode_rows(copy.deepcopy(shaped), series)
    new = MainWindow._episode_rows(copy.deepcopy(shaped), series)
    assert new == old
    # PHP panels: seasons keyed 0..n come as a list of lists, the index the season.
    rows = MainWindow._episode_rows({"episodes": [[_ep(1, 1)], [_ep(11, 1), _ep(12, 2)]]}, series)
    assert [(r["id"], r["season"]) for r in rows] == [("1", "0"), ("11", "1"), ("12", "1")]
    assert rows[1]["name"].startswith("S1 * E1") and rows[1]["_series_title"] == "Show"
    # A flat list: each row says its season.
    rows = MainWindow._episode_rows({"episodes": [_ep(21, 1, season=2), _ep(11, 1, season="1"),
                                                  "junk", None]}, series)
    assert [(r["id"], r["season"]) for r in rows] == [("21", 2), ("11", "1")]
    assert rows[0]["name"].startswith("S2 * E1")
    assert [s.number for s in sv.seasons(rows)] == [1, 2]
    # Nothing usable: no rows, never an exception.
    for info in ({}, {"episodes": None}, {"episodes": []}, {"episodes": {"1": None}}, None, []):
        assert MainWindow._episode_rows(info, series) == []


def test_a_backdrop_for_a_title_matched_before_backdrops_were_kept(app, tmp_path):
    """A TMDB entry cached before 1.6.0 has no backdrop_url: its details are
    asked for again by its id, once, on the pool - the backdrop (or None)
    kept beside what was there; the cache version stays."""
    from unittest.mock import MagicMock

    from PyQt6.QtCore import QSettings, QThreadPool
    from dopeiptv.providers.metadata import PosterResolver
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    client = MagicMock()
    client.fetch_details_by_id.return_value = {
        "tmdb_id": 7, "poster_url": "https://tmdb/other.jpg",
        "backdrop_url": "https://tmdb/w780/b.jpg"}
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    r = PosterResolver(pool, settings, client)
    key = r._key("Show", "series")
    r._cache[key] = {"tmdb_id": 7, "poster_url": "https://tmdb/p.jpg", "manual": True}
    got = []
    assert r.fill_backdrop("Show", "series", got.append) is True
    end = time.time() + 5
    while not got and time.time() < end:
        pool.waitForDone(50)
        app.processEvents()
    assert got and got[0]["backdrop_url"] == "https://tmdb/w780/b.jpg", got
    entry = r.get_full("Show", "series", lambda d: None)
    assert entry["backdrop_url"] == "https://tmdb/w780/b.jpg"
    assert entry["poster_url"] == "https://tmdb/p.jpg" and entry["manual"]   # kept
    # Asked for once: never again, not for an entry that has its backdrop.
    assert r.fill_backdrop("Show", "series", got.append) is False
    r._cache[r._key("Other", "series")] = {"tmdb_id": 8, "backdrop_url": None}
    assert r.fill_backdrop("Other", "series", got.append) is False
    assert r.fill_backdrop("Unknown", "series", got.append) is False
    assert client.fetch_details_by_id.call_count == 1
    assert client.fetch_details_by_id.call_args[0] == (7, "series")
