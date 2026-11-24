"""Long use: what the window keeps after hundreds of the same thing.

Zapping between channels, going from place to place, opening and closing
Settings (every page built), the cast window and a series' page, again and
again: after a warm-up - the first time of each fills its caches - nothing
of it may stay behind. Each is done in rounds, and what is alive is counted
after each: the window's objects, every widget, the top-level windows, the
objects Python tracks, and what Python has allocated (tracemalloc). Growth
that does not level off is a leak: a window never deleted, a connection
kept, a cache without a bound. The counts do not depend on the machine;
the budgets are about three times what the window did when they were set
(docs/PERFORMANCE.md, with the numbers and the longer run).

Nothing goes out: the update check and TMDB are off, the radio directory
answers nothing, the cast search finds nothing and ffprobe is not asked,
and a name that is not this computer's is not looked up at all.
"""
from __future__ import annotations

import os

import pytest

from tests import perf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: Each thing: (done once to warm up, then this many times a round, rounds).
PLAN = {
    "zap": (30, 40, 4),         # 160 zaps measured; long: 1 000
    "places": (18, 18, 3),      # every place, 54 times; long: 450
    "settings": (1, 2, 2),      # Settings with every page built, 4 times; long: 40
    "cast": (5, 8, 3),          # the cast window, 24 times; long: 200
    "series": (4, 6, 3),        # a series' page opened and closed, 18 times; long: 150
}
if perf.LONG:
    PLAN = {"zap": (30, 100, 10), "places": (18, 45, 10), "settings": (1, 4, 10),
            "cast": (5, 20, 10), "series": (4, 15, 10)}

#: What may grow from after the warm-up to after the last round, at most:
#: QObjects under the window, widgets, top-level windows, objects Python
#: tracks, and KB Python allocated. Measured: no window object, widget or
#: window at all (each cast window had stayed: 84 objects, 53 widgets and a
#: window a time), at most 65 Python objects and 28 KB - the last few
#: hundred log lines an error report shows (core/log.py), a time noted.
GROWTH = {"children": 3, "widgets": 3, "toplevel": 0, "gc": 200, "py_kb": 128}
#: Milliseconds each takes, at most (the quickest round): about three times
#: what each took - a zap 25 ms, a place 30 ms, Settings with every page
#: built 555 ms, the cast window 25 ms, a series' page 47 ms.
MS_PER = {"zap": 75, "places": 90, "settings": 1700, "cast": 75, "series": 150}

_CHILD = r"""
import collections, gc, tracemalloc
PLAN = %(plan)r

from PyQt6.QtCore import QCoreApplication, QEvent, QObject
from PyQt6.QtWidgets import QDialog
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import cast_dialog
from dopeiptv.ui import main_window as mw
from dopeiptv.ui.settings_ui import SettingsShell


class Client(DemoClient):
    # The demo source with films and series, all made up.
    def vod_categories(self):
        return [{"category_id": "m", "category_name": "Films"}]

    def vod_streams(self, category_id=None):
        return [{"stream_id": 9000 + i, "name": "Film %%d (2020)" %% i, "category_id": "m",
                 "container_extension": "mp4", "stream_icon": "", "added": str(1700000000 + i)}
                for i in range(40)]

    def series_categories(self):
        return [{"category_id": "s", "category_name": "Shows"}]

    def series_list(self, category_id=None):
        return [{"series_id": 700 + i, "name": "Show %%d" %% i, "category_id": "s", "cover": "",
                 "last_modified": str(1700000000 + i)} for i in range(20)]

    def series_info(self, series_id):
        return {"info": {"name": "Show %%s" %% series_id, "plot": "A plot."},
                "episodes": {str(n): [{"id": "%%s%%d%%02d" %% (series_id, n, e),
                                       "title": "S%%d E%%d" %% (n, e), "episode_num": e,
                                       "season": n, "container_extension": "mkv",
                                       "info": {"duration_secs": 1500}}
                                      for e in range(1, 9)] for n in (1, 2)}}


s = settings()
w = mw.MainWindow(Client(), s, PlaylistStore(s))
w.show()
pump()
wait(1.0)
if w.player is not None:
    w.player.play = lambda url, title, *a, **k: True
mw.launch_player = lambda *a, **k: None
w.wake.acquire = lambda *a, **k: None
w.wake.release = lambda *a, **k: None
type(w).SETTINGS_KEEP_MS = 0
QDialog.exec = lambda self: (self.findChild(SettingsShell).build_all()
                             if self.findChild(SettingsShell) is not None else None, 0)[1]
cast_dialog.probe_tracks = lambda *a, **k: {}
w.cast.scan = lambda *a, **k: []
channels = w.client.live_streams()
series = w.client.series_list()
PLACES = ["mitt", "tv", "films", "series", "guide", "search", "local", "radio", "rec"]


def zap(i):
    w.play_live_channel(channels[i %% len(channels)])
    pump(2)


def place(i):
    w._show_place(PLACES[i %% len(PLACES)])
    pump(3)


def settings(i):
    w.open_settings()
    pump(3)


def cast(i):
    w._open_cast_dialog(dict(channels[i %% len(channels)]))
    pump(3)


def series_page(i):
    w._open_series_page(dict(series[i %% len(series)]))
    wait(0.03)
    pump(3)
    w._close_series_page()
    pump(2)


DO = {"zap": zap, "places": place, "settings": settings, "cast": cast, "series": series_page}


def alive(types=False):
    wait(0.05)
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        pump(2)
    gc.collect()
    # Read before this count makes anything of its own.
    py_kb = tracemalloc.get_traced_memory()[0] // 1024
    objs = gc.get_objects()
    out = {"children": len(w.findChildren(QObject)), "widgets": len(QApplication.allWidgets()),
           "toplevel": len(QApplication.topLevelWidgets()), "gc": len(objs), "py_kb": py_kb}
    if types:
        out["types"] = collections.Counter(type(o).__name__ for o in objs)
    del objs
    return out


tracemalloc.start()
result = {}
for kind, (warm, per, rounds) in PLAN.items():
    fn = DO[kind]
    w._show_place("tv")             # a channel's row is what is cast
    pump(3)
    made = []
    if kind == "cast":
        real_init = cast_dialog.CastDialog.__init__
        cast_dialog.CastDialog.__init__ = (
            lambda self, *a, _real=real_init, **k: (made.append(1), _real(self, *a, **k))[1])
    for i in range(warm):
        fn(i)
    base = alive(types=True)
    base_types = base.pop("types")
    # What this count of types takes is there in every round too.
    base["py_kb"] = tracemalloc.get_traced_memory()[0] // 1024
    seen, took = [], []
    for r in range(rounds):
        t0 = time.perf_counter()
        for i in range(per):
            fn(i)
        took.append((time.perf_counter() - t0) / per * 1000)
        seen.append(alive(types=r == rounds - 1))
    grew = seen[-1].pop("types") - base_types
    # The quickest round: a busy moment of the machine's is not the window's.
    result[kind] = {"base": base, "rounds": seen, "ms": min(took), "ms_rounds": took,
                    "grew": grew.most_common(8), "made": len(made)}
result["tried"] = sorted(set(tried))
done(result)
"""


@pytest.fixture(scope="module")
def seen():
    pytest.importorskip("PyQt6.QtWidgets")
    return perf.run_child(_CHILD % {"plan": PLAN}, timeout=900)


def test_each_thing_was_done_and_nothing_went_out(seen):
    warm, per, rounds = PLAN["cast"]
    assert seen["cast"]["made"] == warm + per * rounds      # a cast window each time
    assert seen["tried"] == []


@pytest.mark.parametrize("kind", sorted(PLAN))
def test_nothing_stays_behind(seen, kind):
    got = seen[kind]
    base, last = got["base"], got["rounds"][-1]
    for what, most in GROWTH.items():
        assert last[what] - base[what] <= most, (kind, what, base[what],
                                                 [r[what] for r in got["rounds"]], got["grew"])


@pytest.mark.parametrize("kind", sorted(PLAN))
def test_each_within_its_time(seen, kind):
    assert seen[kind]["ms"] <= MS_PER[kind], (kind, round(seen[kind]["ms"], 1))
