"""Following a series: what counts as a new episode, and what the window
does with one (toast, desktop notification, Home shelf, the badge and the
jump when the series is opened)."""
import os
import subprocess
import sys

import pytest

from dopeiptv.core.stores import FollowedSeriesStore, episode_rows

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeSettings:
    def __init__(self):
        self._v = {}

    def value(self, key, default=None):
        return self._v.get(key, default)

    def setValue(self, key, val):
        self._v[key] = val


def _info(*eps):
    """A get_series_info answer: seasons as a dict, as panels send them."""
    seasons = {}
    for season, num, eid, added in eps:
        seasons.setdefault(str(season), []).append(
            {"id": str(eid), "episode_num": num, "season": season,
             "title": f"Show - S{season:02d}E{num:02d}", "added": str(added)})
    return {"info": {"name": "Show"}, "episodes": seasons}


def test_episode_rows_reads_dicts_lists_and_junk():
    rows = episode_rows(_info((1, 1, 101, 5), (1, 2, 102, 6)))
    assert [r["id"] for r in rows] == ["101", "102"]
    assert rows[0] == {"id": "101", "season": "1", "episode_num": "1",
                       "title": "Show - S01E01", "added": 5}
    flat = {"episodes": [{"id": 7, "season": 2, "episode_num": 3}]}
    assert episode_rows(flat)[0]["season"] == "2"
    assert episode_rows({"episodes": {"1": [{"title": "no id"}, None]}}) == []
    assert episode_rows(None) == [] and episode_rows([]) == []
    assert episode_rows({"episodes": {"1": [{"id": 1, "added": "x"}]}})[0][
        "added"] == 0


def test_first_look_learns_then_new_episodes_are_news():
    st = FollowedSeriesStore(FakeSettings(), "p1")
    st.follow({"series_id": 12, "name": "Show", "cover": "http://c/1.jpg",
               "plot": "not kept"})
    assert st.is_followed(12) and st.is_followed("12")
    assert "plot" not in st.snapshot(12)
    # Everything already out when you follow is not news.
    assert st.update(12, _info((1, 1, 101, 5), (1, 2, 102, 6)), now=100) == []
    assert st.with_new() == []
    fresh = st.update(12, _info((1, 1, 101, 5), (1, 2, 102, 6),
                                (1, 3, 103, 7)), now=200)
    assert [e["id"] for e in fresh] == ["103"]
    assert st.new_ids(12) == ["103"]
    # Asked again before it was opened: still one new, not two.
    assert st.update(12, _info((1, 1, 101, 5), (1, 2, 102, 6),
                               (1, 3, 103, 7)), now=300) == []
    assert st.new_ids(12) == ["103"]
    (row,) = st.with_new()
    assert row["snap"]["name"] == "Show" and len(row["new"]) == 1


def test_an_empty_answer_is_a_hiccup_not_news():
    st = FollowedSeriesStore(FakeSettings())
    st.follow({"series_id": 1, "name": "A"})
    st.update(1, _info((1, 1, 11, 1)), now=10)
    assert st.update(1, {"episodes": {}}, now=20) == []
    assert st.update(1, {}, now=30) == []
    # ...and the next proper answer does not call the old episode new.
    assert st.update(1, _info((1, 1, 11, 1)), now=40) == []
    assert st.series["1"]["checked"] == 40


def test_opening_the_series_marks_it_seen():
    st = FollowedSeriesStore(FakeSettings())
    st.follow({"series_id": 5, "name": "B"})
    st.update(5, _info((1, 1, 51, 1)))
    st.update(5, _info((1, 1, 51, 1), (1, 2, 52, 2)))
    assert st.new_ids(5) == ["52"]
    st.seen(5, _info((1, 1, 51, 1), (1, 2, 52, 2), (1, 3, 53, 3)))
    assert st.new_ids(5) == [] and st.with_new() == []
    # What the opened list showed is known: not announced later.
    assert st.update(5, _info((1, 1, 51, 1), (1, 2, 52, 2),
                              (1, 3, 53, 3))) == []
    # Opening before the first look did the priming itself.
    st.follow({"series_id": 6, "name": "C"})
    st.seen(6, _info((1, 1, 61, 1)))
    assert st.series["6"]["primed"]
    assert [e["id"] for e in st.update(
        6, _info((1, 1, 61, 1), (1, 2, 62, 2)))] == ["62"]


def test_newest_first_capped_persisted_and_due():
    settings = FakeSettings()
    st = FollowedSeriesStore(settings, "p")
    for sid in (1, 2):
        st.follow({"series_id": sid, "name": f"S{sid}"})
        st.update(sid, _info((1, 1, sid * 100, 1)), now=1000)
    st.update(1, _info((1, 1, 100, 1), (1, 2, 101, 50)), now=2000)
    st.update(2, _info((1, 1, 200, 1), (1, 2, 201, 90)), now=1500)
    assert [r["snap"]["name"] for r in st.with_new()] == ["S2", "S1"]
    # Oldest-asked first; one asked recently is not due.
    assert st.due(now=2100, min_age=500) == ["2"]
    assert st.due(now=9000, min_age=500) == ["2", "1"]
    # Survives a restart, per playlist.
    again = FollowedSeriesStore(settings, "p")
    assert again.ids() == ["1", "2"] and again.new_ids(2) == ["201"]
    assert FollowedSeriesStore(settings, "other").ids() == []
    # A flood of new episodes keeps the latest MAX_NEW.
    many = [(1, n, 1000 + n, n) for n in range(1, 80)]
    st.update(1, _info((1, 1, 100, 1), (1, 2, 101, 50), *many), now=3000)
    assert len(st.new_ids(1)) == FollowedSeriesStore.MAX_NEW
    assert st.new_ids(1)[-1] == "1079"
    st.unfollow(1)
    assert not st.is_followed(1) and st.ids() == ["2"]


def test_episode_label():
    pytest.importorskip("PyQt6")
    from dopeiptv.ui.mw_follow import episode_label
    assert episode_label({"season": "2", "episode_num": "5"}) == "S2 E5"
    assert episode_label({"title": "Pilot"}) == "Pilot"
    assert episode_label({"episode_num": "3"}) == "E3"


_CHILD = r"""
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QLabel, QMenu

from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.services import notify as notify_mod
from dopeiptv.ui import mw_follow
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "follow-child")
settings.clear()
w = MainWindow(DemoClient(), settings)

answers = {"12": {"episodes": {"1": [
    {"id": "101", "episode_num": 1, "title": "Show - S01E01", "added": "5"},
    {"id": "102", "episode_num": 2, "title": "Show - S01E02", "added": "6"},
]}}}
w.client.series_info = lambda sid: answers[str(sid)]
notes = []
notify_mod.notify = lambda *a: notes.append(a) or True

def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out"

# The context menu offers Follow, then Stop following.
show = {"series_id": 12, "name": "Fav Series", "cover": "http://x/c.jpg"}
m = QMenu()
w._add_follow_action(m, show)
assert [a.text() for a in m.actions()] == [tr("ctx_follow_series")]
w._follow_series(show)
settle(lambda: w.followed.series["12"]["primed"])
assert w.followed.new_ids(12) == []        # what was out is not news
m = QMenu()
w._add_follow_action(m, show)
assert [a.text() for a in m.actions()] == [tr("ctx_unfollow_series")]

# The check finds a new episode: a toast, and the desktop is told.
answers["12"]["episodes"]["1"].append(
    {"id": "103", "episode_num": 3, "title": "Show - S01E03", "added": "9"})
mw_follow.MIN_AGE_S = 0
toasts = []
w._show_toast = lambda text, ms=0: toasts.append(text)
w._check_followed()
settle(lambda: not w._follow_running)
want = tr("follow_new_episode", name="Fav Series", episode="S1 E3")
assert toasts == [want], toasts
assert notes and notes[0][0] == tr("home_new_episodes") and notes[0][1] == want

# Home shows it on the New episodes shelf.
w._show_home_page(); app.processEvents()
texts = " | ".join(l.text() for l in w._home_page.findChildren(QLabel))
assert tr("home_new_episodes") in texts and "Fav Series" in texts, texts
assert "S1 E3" in texts, texts

# Opening the series flags the new episode, selects it, and it is seen.
w._leave_home()
eps = [dict(e) for e in answers["12"]["episodes"]["1"]]
w._pending_jump_key = None
w._follow_entered(12, answers["12"], eps)
assert [e.get("_new", False) for e in eps] == [False, False, True]
assert w._pending_jump_key == "103"
assert w.followed.new_ids(12) == [] and w.followed.with_new() == []
w._pending_jump_key = None

# A check with nothing new says nothing.
toasts.clear()
w._check_followed()
settle(lambda: not w._follow_running)
assert toasts == [], toasts

# Two series at once: one summary line.
w.followed.follow({"series_id": 13, "name": "Other"})
answers["13"] = {"episodes": {"1": [{"id": "301", "episode_num": 1}]}}
w._check_followed(); settle(lambda: not w._follow_running)
answers["12"]["episodes"]["1"].append({"id": "104", "episode_num": 4})
answers["13"]["episodes"]["1"].append({"id": "302", "episode_num": 2})
toasts.clear()
w._check_followed(); settle(lambda: not w._follow_running)
assert toasts == [tr("follow_new_series", n=2)], toasts

# Announcements switched off under Settings -> Home: nothing is said, and
# the new episode is still noted for the shelf and the dots.
settings.setValue("follow_notify", "false")
answers["12"]["episodes"]["1"].append({"id": "105", "episode_num": 5})
toasts.clear()
notes.clear()
w._check_followed(); settle(lambda: not w._follow_running)
assert toasts == [] and notes == [], (toasts, notes)
assert "105" in w.followed.new_ids(12)
settings.setValue("follow_notify", "true")

w._unfollow_series(show)
assert not w.followed.is_followed(12)
print("FOLLOW_OK")
"""


def test_window_follows_and_announces(tmp_path):
    pytest.importorskip("PyQt6")
    # Followed series live in a settings file of their own in the user's
    # config directory: a fresh one, so no earlier run is remembered.
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    assert "FOLLOW_OK" in proc.stdout, (
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}")
