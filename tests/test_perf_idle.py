"""Idle: the window shown, nothing playing - how often the app wakes.

Each wake is a timer the window's thread answers, or a thread of the app's
own that comes out of its sleep; on a laptop each one costs the battery,
and a ticker left running where nothing shows is a ticker for nothing. The
counts here do not depend on the machine: a timer that fires once a second
fires once a second on CI too. The budgets are about three times what the
window did when they were set (docs/PERFORMANCE.md, where the numbers and
how to run this longer are).
"""
from __future__ import annotations

import os
import sys

import pytest

from tests import perf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: Timer events a second on the window's thread, with every place visited
#: and left, back on TV, nothing playing. Measured 0.4 (the recordings'
#: 5 s look at what is due, the 12 s keep of where a film got to).
IDLE_TIMERS_PER_S = 1.2
#: Wakes of the app's threads a second (Linux: voluntary context switches,
#: every thread of the process) over the same stretch. Measured 0.8.
IDLE_WAKES_PER_S = 2.5
#: Its CPU time over that stretch, as a share of it. Measured 0.02 % - this
#: one is left loose (2 %): a stray log line or a collection on a shared
#: runner is no regression, a loop that polls is.
IDLE_CPU_SHARE = 0.02
#: Timer events a second while a place is on screen, each measured for a
#: moment as a person comes back to it (every place visited once before),
#: the start's work - the first logos, a notice fading - still settling.
#: Mine, Movies, Series and Search have a text field with the focus, whose
#: cursor blinks twice a second; Mine's page looks at what plays once a
#: second, the guide moves its clock line every 1.5 s. Measured, the most
#: of four runs: Mine 3.3, TV 5, Movies 6.7, Series 5, the Guide 1.7,
#: Search 1.7, Local files 0, Radio 1.7, Recordings 0 - the window's own
#: ticker of 100 ms or less on any of them breaks it.
PLACE_TIMERS_PER_S = 15.0

STRETCH = 10.0 if perf.LONG else 2.5
PLACE_STRETCH = 4.0 if perf.LONG else 0.6

_CHILD = r"""
from PyQt6.QtCore import QEvent, QObject
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw

STRETCH, PLACE_STRETCH = %(stretch)r, %(place_stretch)r


class Timers(QObject):
    def __init__(self):
        super().__init__()
        self.on, self.seen = False, {}
        self.clock = QTimer()           # its own, left out of the count
        self.clock.setSingleShot(True)

    def eventFilter(self, obj, ev):
        if self.on and ev.type() == QEvent.Type.Timer and obj is not self.clock:
            who = type(obj).__name__
            parent = obj.parent() if hasattr(obj, "parent") else None
            if hasattr(obj, "interval"):
                who += "[%%d ms]" %% obj.interval()
            who += " of " + (type(parent).__name__ if parent is not None else "-")
            self.seen[who] = self.seen.get(who, 0) + 1
        return False

    def count(self, sec):
        self.seen = {}
        loop = QEventLoop()
        self.clock.timeout.connect(loop.quit)
        self.on = True
        self.clock.start(int(sec * 1000))
        loop.exec()
        self.on = False
        self.clock.timeout.disconnect(loop.quit)
        return sum(self.seen.values()) / sec, sorted(self.seen.items(), key=lambda kv: -kv[1])[:6]


def wakes():
    total = 0
    for tid in os.listdir("/proc/self/task"):
        try:
            with open("/proc/self/task/%%s/status" %% tid) as f:
                for line in f:
                    if line.startswith("voluntary_ctxt_switches"):
                        total += int(line.split()[1])
        except OSError:
            pass
    return total


s = settings()
w = mw.MainWindow(DemoClient(), s, PlaylistStore(s))
w.show()
pump()
out = {"player": w.player is not None}
# Mine at once, while the start's channel list is still on its way: the
# list's spinner is behind Mine's page now.
w._show_place("mitt")
wait(1.0)
spin = getattr(w, "_busy_spin_timer", None)
out["spinner_hidden_turning"] = bool(spin is not None and spin.isActive()
                                     and not w._busy_overlay.isVisible())
timers = Timers()
app.installEventFilter(timers)
PLACES = ("mitt", "tv", "films", "series", "guide", "search", "local", "radio", "rec")
# Each place once first - its page built, its list and logos asked for -
# then each measured on screen as a person comes back to it.
for place in PLACES:
    w._show_place(place)
    wait(0.2)
places = {}
for place in PLACES:
    w._show_place(place)
    wait(0.1)
    places[place] = timers.count(PLACE_STRETCH)
out["places"] = places
w._show_place("tv")
wait(1.5)
out["ts_timer"] = w._ts_timeline_timer.isActive()
linux = os.path.isdir("/proc/self/task")
w0, c0 = (wakes() if linux else 0), time.process_time()
rate, top = timers.count(STRETCH)
out["idle"] = {"timers": rate, "top": top, "cpu": (time.process_time() - c0) / STRETCH,
               "wakes": ((wakes() - w0) / STRETCH) if linux else None}
out["tried"] = sorted(set(tried))
done(out)
"""


@pytest.fixture(scope="module")
def seen():
    pytest.importorskip("PyQt6.QtWidgets")
    return perf.run_child(_CHILD % {"stretch": STRETCH, "place_stretch": PLACE_STRETCH})


def test_idle_window_wakes_within_its_budget(seen):
    idle = seen["idle"]
    assert idle["timers"] <= IDLE_TIMERS_PER_S, idle
    assert idle["cpu"] <= IDLE_CPU_SHARE, idle
    if idle["wakes"] is not None:
        assert idle["wakes"] <= IDLE_WAKES_PER_S, idle


@pytest.mark.parametrize("place", ["mitt", "tv", "films", "series", "guide", "search",
                                   "local", "radio", "rec"])
def test_each_place_on_screen_within_its_budget(seen, place):
    rate, top = seen["places"][place]
    assert rate <= PLACE_TIMERS_PER_S, (place, rate, top)


def test_the_timeshift_timeline_ticks_only_while_shown(seen):
    """It ran once a second from the start to the end of every session -
    to find, each time, that no timeline was on screen."""
    if not seen["player"]:
        pytest.skip("no embedded player here")
    assert seen["ts_timer"] is False


def test_the_list_spinner_turns_only_while_seen(seen):
    """The start's channel list still on its way and Mine shown over it:
    the spinner behind Mine's page had turned nine times a second until the
    list's job gave up, 25 s later."""
    assert seen["spinner_hidden_turning"] is False


# -- the helper itself, without a window ---------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


def test_a_timer_runs_only_while_its_widget_is_shown(qapp):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.ui.widgets import WhileShown
    top = QWidget()
    child = QWidget(top)
    timer = QTimer()
    timer.setInterval(1000)
    shown = []
    WhileShown(child, timer, lambda: shown.append(1))
    assert not timer.isActive()             # nothing on screen yet
    top.show()
    assert timer.isActive()                 # shown with its parent
    qapp.processEvents()
    assert shown == [1]                     # told once, at once
    top.hide()
    assert not timer.isActive()             # its parent hidden
    top.show()
    assert timer.isActive()
    child.hide()
    assert not timer.isActive()             # itself hidden
    top.hide()
    top.show()
    assert not timer.isActive()             # still hidden itself
    top.deleteLater()


def test_the_busy_spinner_stops_behind_a_page(qapp):
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.ui.mw_busy import _BusyMixin

    class Model:
        def rowCount(self):
            return 0

    class Host(QWidget, _BusyMixin):
        pass

    h = Host()
    h.listw = QWidget(h)
    h.list_model = Model()
    h.show()
    h._update_busy_overlay("Loading channels…")
    assert h._busy_spin_timer.isActive()
    h.listw.hide()                          # a page over the list
    assert not h._busy_spin_timer.isActive()
    h._update_busy_overlay("Loading channels…")
    assert not h._busy_spin_timer.isActive()    # asked again, still unseen
    h.listw.show()
    assert h._busy_spin_timer.isActive()    # back, it turns again
    h._hide_busy_overlay()
    assert not h._busy_spin_timer.isActive()
    h.deleteLater()


def test_nothing_went_out(seen):
    """What the idle window measures never waited on the internet."""
    assert seen["tried"] == []
