"""The Recordings place (core/rec_view.py, ui/rec_place.py, ui/mw_place_rec.py).

Without a window: what records now before what is to come, how long one has
run, the day a recording is of (one to come is no "today"), its length from
what says it, where its frame is taken, its name without the file's date,
the watched marks (marked, moved, dropped, bounded), a frame and length
found once and read back after. In the window: the place where the list was
(the list its source of truth), its header (name, count with the size, the
filter, ⋯, Grid / List remembered), "Recording now and next" over All
recordings, a recording played through the window's path and its menu the
list's own, Watched once played to its end, a list of recordings to come
with their state."""
import json
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


def test_now_and_next_and_the_words():
    from dopeiptv.core import rec_view as rv
    jobs = [{"id": "a", "status": "scheduled", "start": 300},
            {"id": "b", "status": "done", "start": 50},
            {"id": "c", "status": "recording", "start": 200},
            {"id": "d", "status": "scheduled", "start": 100},
            {"id": "e", "status": "recording", "start": 20}]
    assert [j["id"] for j in rv.pending(jobs)] == ["e", "c", "d", "a"]
    assert rv.elapsed_text(42) == "0:42"
    assert rv.elapsed_text(754) == "12:34"
    assert rv.elapsed_text(3723) == "1:02:03"
    assert rv.elapsed_text(-5) == "0:00"
    import datetime
    noon = datetime.datetime(2026, 11, 17, 12, 0).timestamp()
    day = 86400
    assert rv.day_of(noon - 3600, noon) == "today"
    assert rv.day_of(noon - day, noon) == "yesterday"
    assert rv.day_of(noon - 3 * day, noon) == "week"
    assert rv.day_of(noon - 30 * day, noon) == "earlier"
    assert rv.day_of(noon + day, noon) == "later"          # tomorrow is no "today"
    assert rv.length_of(2700.0, 100.0, None) == 2700.0
    assert rv.length_of(0.0, 1800.0, None) == 1800.0
    assert rv.length_of(0, 0, {"start": 100, "stop": 400, "prog_start": 160,
                               "prog_stop": 360}) == 200
    assert rv.length_of(0, 0, {"start": 100, "stop": None}) == 0.0
    assert rv.seek_for(0) == 120.0 and rv.seek_for(3600) == 360.0
    assert rv.seek_for(20) == 3.0 and rv.seek_for(4) == 2.0
    assert rv.shown_title("Evening News 2026-11-17 21.00") == "Evening News"
    assert rv.shown_title("Evening News 2026-11-17 21.00 (2)") == "Evening News"
    assert rv.shown_title("2026-11-17 21.00") == "2026-11-17 21.00"
    assert rv.shown_title("My own name") == "My own name"


def test_watched_marks():
    from dopeiptv.core import rec_view as rv
    s = _Settings()
    marks = rv.WatchedMarks(s)
    assert not marks.has("/r/a.ts")
    marks.mark("/r/a.ts", when=10)
    marks.mark("/r/b.ts", when=20)
    assert marks.has("/r/a.ts") and json.loads(s[rv.WATCHED_KEY]) == {"/r/a.ts": 10,
                                                                     "/r/b.ts": 20}
    marks.moved("/r/a.ts", "/r/Folder/a.ts")
    assert not marks.has("/r/a.ts") and marks.has("/r/Folder/a.ts")
    marks.unmark("/r/b.ts")
    assert not marks.has("/r/b.ts")
    again = rv.WatchedMarks(s)                              # read back
    assert again.has("/r/Folder/a.ts")
    assert rv.parse("not json") == {} and rv.parse('{"x": "y", "z": 3}') == {"z": 3}
    big = {f"/r/{i}.ts": i for i in range(rv.WATCHED_MAX + 50)}
    kept = rv.bounded(big)
    assert len(kept) == rv.WATCHED_MAX and "/r/0.ts" not in kept
    assert f"/r/{rv.WATCHED_MAX + 49}.ts" in kept


def test_a_frame_and_length_found_once(tmp_path):
    from dopeiptv.core import rec_view as rv
    thumbs = str(tmp_path / "thumbs")
    runs = []

    def run(argv, timeout):
        runs.append(argv)
        if argv[-1].endswith(".jpg"):
            assert argv[argv.index("-ss") + 1] == "270"     # a tenth of the way in
            with open(argv[-1], "wb") as fh:
                fh.write(b"jpg")
            return 0, ""
        return 1, "  Duration: 00:45:00.00, start: 1.4, bitrate: 3000 kb/s\n"
    got = rv.probe("ffmpeg", thumbs, "/r/a.ts", run)
    assert got.length == 2700.0 and got.frame == rv.frame_path(thumbs, "/r/a.ts")
    assert len(runs) == 2
    again = rv.probe("ffmpeg", thumbs, "/r/a.ts", run)       # kept: nothing asked
    assert again == got and len(runs) == 2
    # No ffmpeg: nothing found, nothing run.
    assert rv.probe("", thumbs, "/r/b.ts", run) == rv.Probe() and len(runs) == 2


def test_a_recording_keeps_its_channel_and_start(tmp_path):
    from PyQt6.QtCore import QSettings
    from dopeiptv.core.recording import RecordingManager
    vid = tmp_path / "News 2026-11-17 21.00.ts"
    vid.write_bytes(b"\0" * 16)
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("recordings_dir", str(tmp_path))
    rm = RecordingManager(s)
    rm.write_info(str(vid), "", "", icon="", channel="SVT1", start=1763409600)
    row = rm.files(None)[0]
    assert row["_channel"] == "SVT1" and row["_start"] == 1763409600
    rm.write_info(str(vid), "My title", "")                 # an edit keeps them
    row = rm.files(None)[0]
    assert row["name"] == "My title" and row["_channel"] == "SVT1"
    assert row["_start"] == 1763409600
    rm.drop_info(str(vid))
    assert not (tmp_path / (vid.name + ".info.json")).exists()
    assert rm.files(None)[0]["_channel"] == ""


def test_the_page_on_a_narrow_pane(app):
    from dopeiptv.ui.rec_place import RecPlace
    place = RecPlace(None)
    place.show_layout_switch(True, "wide")
    place.resize(1200, 600)
    place.show()
    app.processEvents()
    assert not place.is_narrow() and place.more.isVisible() and place.layout_switch.isVisible()
    place.resize(480, 600)
    app.processEvents()
    assert place.is_narrow() and place._tools.isVisible() and place.more.isVisible()
    place.resize(150, 600)                          # the narrowest: Grid / List left out
    app.processEvents()
    assert place.width() < place.tools_width()
    assert not place.layout_switch.isVisible() and place.more.isVisible()
    assert place.minimumSizeHint().width() <= 150, place.minimumSizeHint()
    place.show_tool(place.more, False)
    app.processEvents()
    assert not place.more.isVisible()
    place.resize(1200, 600)
    app.processEvents()
    assert not place.is_narrow() and not place.more.isVisible()
    place.show_tool(place.more, True)
    app.processEvents()
    assert place.more.isVisible()
    place.deleteLater()


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    a = QApplication.instance() or QApplication([])
    from dopeiptv.ui import theme as th
    th.apply_theme(theme="graphite")
    return a


_CHILD = r"""
import json, os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QPoint, QSettings, Qt
from PyQt6.QtWidgets import QApplication

tree = sys.argv[1]
_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)
import dopeiptv.providers.cast_bridge as cb
cb.ffmpeg_path = lambda: None
from dopeiptv.core._lazy_requests import requests as rq
def offline(*_a, **_k):
    raise OSError("offline in the test")
rq.get = offline
from dopeiptv.core import rec_view
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod, mw_place_rec
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
settings.setValue("recordings_dir", tree)
mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
played = []
w._start_playback = lambda url, title, icon, key, kind, **k: played.append((url, kind))
menus = []
w._rec_context_menu = lambda pos, it, at=None: menus.append((it.get("_key"), at))
w.resize(1440, 900)
w.show()

def settle(cond, secs=20, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what

settle(lambda: w.list_model.rowCount() > 0, what="first list")
now = time.time()
w.rec.add_job("http://x/2", "Evening News", now + 3 * 3600, now + 4 * 3600, channel="SVT1")

# -- the place where the list was ----------------------------------------------------
assert w._show_place("rec") is True
settle(lambda: w._place_rec_showing() and w._rp_page.view.model_.rowCount() == 3,
       what="the place")
place = w._rp_page
view = place.view
assert not w.listw.isVisible() and place.isVisible() and w.nav_btns["rec"].isChecked()
h = place.header
assert h.title.text() == tr("nav_recordings"), h.title.text()
assert h.count.text().startswith(tr("rec_n_recordings", n=3) + " · "), h.count.text()
files = [r["_key"] for r in w.list_model.items() if r.get("_kind") == "recording"]
assert [c.key for c in view.items()] == files
assert place.now.isVisible() and [c.title for c in place.now.cards()] == ["Evening News"]
assert place.now.cards()[0].line == "SVT1"
assert place.more.isVisible() and place.layout_switch.isVisible()
assert view.layout_kind == "list" and place.layout_switch.current() == "list"
# A card's words: its name without the file's date, its channel and size.
news = next(c for c in view.items() if "News" in c.title)
view.dress(news)
assert news.title == "Evening News", news.title
assert news.line.startswith("SVT1 · ") and "KB" in news.line, news.line

# Played through the window's path; its menu the list's own, at the card.
view.activated.emit(news)
assert played[-1] == (news.key, "recording"), played
assert w._place_rec_showing()
view.menu.emit(news, QPoint(5, 5))
assert menus[-1] == (news.key, QPoint(5, 5))
cur = w.list_model.item_at(w.listw.currentIndex().row())
assert cur is not None and cur["_key"] == news.key       # the list follows the card

# Watched once played to its end: its card says so.
w._rec_auto_watched(news.key)
assert w._rec_watched().has(news.key)
view.dress(news)
assert "seen" in news.pills, news.pills
w._rec_toggle_watched(news.data)
view.dress(news)
assert "seen" not in news.pills

# Grid / List remembered.
place.layout_switch.buttons[0].click()
assert view.layout_kind == "wide" and settings.value(mw_place_rec.VIEW_KEY) == "wide"

# The filter is the list's: typed in the place, the list filters.
place.filter.setText("Football")
settle(lambda: view.model_.rowCount() == 1, what="filtered")
assert w.search.text() == "Football" and not place.now.isVisible()
place.filter.clear()
settle(lambda: view.model_.rowCount() == 3, what="unfiltered")

# -- recordings to come, with their state ------------------------------------------------
for i in range(w.cat_list.count()):
    if w.cat_list.item(i).data(Qt.ItemDataRole.UserRole) == "__upcoming__":
        w.cat_list.setCurrentRow(i)
settle(lambda: h.title.text() == tr("rec_upcoming"), what="upcoming")
settle(lambda: view.model_.rowCount() == 1, what="one to come")
job = view.items()[0]
view.dress(job)
assert job.title == "Evening News" and ("next", tr("rec_status_scheduled")) in job.pills
assert not place.layout_switch.isVisible() and view.layout_kind == "list"
assert not place.now.isVisible()
view.activated.emit(job)                    # nothing to play: its menu
assert menus[-1][0] == job.key
w.switch_mode("live")
settle(lambda: not w._place_rec_active() and w.listw.isVisible(), what="TV's list")
print("REC_PLACE_OK", flush=True)
os._exit(0)
"""


def _tree(tmp_path) -> str:
    root = tmp_path / "Recordings"
    root.mkdir()
    for name, ch in (("Evening News 2026-11-16 21.00.ts", "SVT1"),
                     ("Football 2026-11-15 18.00.ts", "TV4"), ("Docs.ts", "")):
        p = root / name
        p.write_bytes(b"\0" * 4000)
        if ch:
            (root / (name + ".info.json")).write_text(json.dumps(
                {"title": "", "description": "", "icon": "", "channel": ch,
                 "start": 1763330400}))
    return str(root)


def test_the_recordings_place_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path / "c"),
               XDG_DATA_HOME=str(tmp_path / "d"), XDG_CACHE_HOME=str(tmp_path / "k"))
    proc = subprocess.run([sys.executable, "-c", _CHILD, _tree(tmp_path)],
                          capture_output=True, text=True, env=env, cwd=_REPO_ROOT,
                          timeout=240)
    assert "REC_PLACE_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")
