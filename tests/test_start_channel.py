"""Start on the last channel (core/start_channel.py, ui/mw_start_channel.py):
off by default; on, the profile's last live channel of the source in use
plays once the app has started - never before who is watching is said, over
a command, for a channel the lists would not show now (hidden, of a hidden
or locked category, a kids profile's), with History empty, nor when
something plays already. The window's own checks on the real window run in
a child process (offscreen), as the person meets them."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dopeiptv.core import start_channel as sc

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _S:
    def __init__(self, **kv):
        self.kv = {k: str(v) for k, v in kv.items()}

    def value(self, k, default=None):
        return self.kv.get(k, default)


# -- what it decides with -----------------------------------------------------------

def test_off_unless_switched_on():
    assert not sc.on(_S())
    assert not sc.on(_S(start_last_channel="false"))
    assert sc.on(_S(start_last_channel="true"))
    assert not sc.on(None)


@pytest.mark.parametrize("cmd, skips", [
    (None, False),
    ({"tv": True}, False),                      # TV mode alone: still a TV's start
    ({"play": "http://h/x.ts"}, True),
    ({"channel": "SVT1"}, True),
    ({"channel": "SVT1", "tv": True}, True),
    ({"number": 5}, True),
    ({"open": True}, True),                     # a link that only opens the app
    ({"play": "http://h/x.ts", "link": True}, True),
])
def test_a_command_goes_instead(cmd, skips):
    assert sc.skips(cmd) is skips


def test_the_newest_live_channel_of_history_only():
    film = {"_kind": "movie", "_key": 9, "name": "Film"}
    two = {"_kind": "live", "_key": 2, "stream_id": 2, "name": "Two"}
    one = {"_kind": "live", "_key": 1, "stream_id": 1, "name": "One"}
    assert sc.last_live([film, two, one]) is two
    assert sc.last_live([]) is None and sc.last_live(None) is None
    assert sc.last_live([film]) is None
    # One kept without an id names nothing - no older one in its place.
    assert sc.last_live([{"_kind": "live", "name": "?"}, one]) is None
    assert sc.stream_of({"_kind": "live", "_key": "7"}) == "7"
    assert sc.stream_of({"_kind": "live", "stream_id": {"x": 1}}) == ""


def test_found_by_its_stream_id_among_the_rows_shown():
    rows = [{"stream_id": 1, "name": "One"}, {"_header": True, "stream_id": 2},
            {"stream_id": "2", "name": "Two"}]
    assert sc.find(rows, {"_kind": "live", "stream_id": 2})["name"] == "Two"
    assert sc.find(rows, {"_kind": "live", "stream_id": 3}) is None
    assert sc.find([], {"_kind": "live", "stream_id": 1}) is None
    assert sc.find(rows, {"_kind": "live"}) is None


def test_a_profile_s_own_and_not_synced():
    from dopeiptv.core import profiles, sync
    assert profiles.is_own(sc.KEY)
    assert not sync.syncable(sc.KEY), "a computer's start: as Start in TV mode, not synced"


# -- the window's side, on a stand-in window -----------------------------------------

from dopeiptv.ui import mw_start_channel as msc  # noqa: E402


class W(msc._StartChannelMixin):
    """What the mixin asks of the window."""

    def __init__(self, on=True, history=None, rows=None):
        self.settings = _S(start_last_channel="true" if on else "false")
        self.history = SimpleNamespace(items=lambda: list(history if history is not None else [
            {"_kind": "live", "_key": 2, "stream_id": 2, "name": "Two"},
            {"_kind": "live", "_key": 1, "stream_id": 1, "name": "One"}]))
        self.rows = rows if rows is not None else [{"stream_id": 1, "name": "One"},
                                                   {"stream_id": 2, "name": "Two"}]
        self.client = object()
        self.pool = None
        self.player = SimpleNamespace(current_url=None)
        self._last_playback = None
        self._welcome = None
        self.playlist_store = SimpleNamespace(active=lambda: {"id": "p1"})
        self._profile_gen = 0
        self._profile_must_pick = False
        self._profile_picker = None
        self._tv_on = False
        self._tv_page = None
        self.played: list = []
        self.asked = 0

    def _chno_rows(self, rows):
        return [r for r in rows if not r.get("hidden")]

    def _live_rows_job(self, _client):
        self.asked += 1
        return lambda: list(self.rows)

    def _profile_locked(self):
        return self._profile_must_pick

    def play_live_channel(self, it):
        self.played.append(it["stream_id"])
        self._last_playback = {"kind": "live"}


@pytest.fixture
def now(monkeypatch):
    """Timers and the pool run at once, in order."""
    later: list = []
    monkeypatch.setattr(msc.QTimer, "singleShot", lambda _ms, fn: later.append(fn))
    monkeypatch.setattr(msc.sip, "isdeleted", lambda _o: False)

    def run_async(_pool, job, done, fail=None):
        try:
            got = job()
        except Exception as e:
            if fail:
                fail(e)
            return
        done(got)
    monkeypatch.setattr(msc, "run_async", run_async)

    def go(rounds=5):
        for _ in range(rounds):
            if not later:
                return
            later.pop(0)()
    return go


def test_on_it_plays_the_last_channel_once(now):
    w = W()
    w._start_channel_arm()
    now()
    assert w.played == [2] and w._sc_over == "played"
    w._start_channel_try()
    assert w.played == [2], "once"


def test_off_nothing_plays_and_nothing_is_read(now):
    w = W(on=False)
    w._start_channel_arm()
    now()
    assert w.played == [] and w.asked == 0


def test_never_before_who_is_watching_is_said(now):
    w = W()
    w._profile_must_pick = True                 # a profile's PIN at start
    w._start_channel_arm()
    now(3)
    assert w.played == [] and w.asked == 0
    w._profile_must_pick = False
    w._profile_picker = object()                # the picker still over the window
    now(3)
    assert w.played == []
    w._profile_picker = None
    w._tv_on, w._tv_page = True, SimpleNamespace(current="profiles")   # TV mode's picker
    now(3)
    assert w.played == []
    w._tv_page.current = "home"                 # picked
    now(3)
    assert w.played == [2]


def test_the_profile_picked_decides(now):
    w = W()
    w._profile_picker = object()
    w._start_channel_arm()
    now(2)
    # The one picked has it off: its own switch counts, not the one before's.
    w.settings = _S(start_last_channel="false")
    w._profile_picker = None
    now(3)
    assert w.played == [] and w.asked == 0


def test_a_command_wins(now):
    w = W()
    w._start_channel_arm()
    w._start_channel_skip("the app was started with a command")
    now()
    assert w.played == [] and w.asked == 0
    # One handed over while the channel is looked up wins too.
    w = W()
    w._live_rows_job = lambda _c: (lambda: (w._start_channel_skip("a command came"),
                                            w.rows)[1])
    w._start_channel_arm()
    now()
    assert w.played == []


def test_not_when_something_plays_already(now):
    w = W()
    w._last_playback = {"kind": "movie"}
    w._start_channel_arm()
    now()
    assert w.played == [] and w.asked == 0
    w = W()
    w.player.current_url = "http://x/1"
    w._start_channel_arm()
    now()
    assert w.played == []


def test_not_for_a_channel_the_lists_do_not_show(now):
    w = W(rows=[{"stream_id": 1, "name": "One"}, {"stream_id": 2, "name": "Two", "hidden": True}])
    w._start_channel_arm()
    now()
    assert w.played == [] and w._sc_over == "not found", "not an older channel instead"


def test_history_empty_or_without_a_channel(now):
    for hist in ([], [{"_kind": "movie", "_key": 9}]):
        w = W(history=hist)
        w._start_channel_arm()
        now()
        assert w.played == [] and w.asked == 0


def test_no_source_or_the_welcome(now):
    w = W()
    w.playlist_store = SimpleNamespace(active=lambda: None)
    w._start_channel_arm()
    now()
    assert w.played == []
    w = W()
    w._welcome = SimpleNamespace(isVisible=lambda: True)
    w._start_channel_arm()
    now()
    assert w.played == []


def test_a_source_or_profile_changed_meanwhile_plays_nothing(now):
    w = W()

    def job(_c):
        def read():
            w.client = object()                 # another source, meanwhile
            return w.rows
        return read
    w._live_rows_job = job
    w._start_channel_arm()
    now()
    assert w.played == []


def test_a_failure_is_never_in_the_way(now):
    w = W()
    w.history = SimpleNamespace(items=lambda: 1 / 0)
    w._start_channel_arm()
    now()
    assert w.played == [] and w._sc_over == "failed"


# -- on the real window ---------------------------------------------------------------

_CHILD = r"""
import faulthandler, json, os, sys, tempfile, time
faulthandler.dump_traceback_later(120, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from dopeiptv.ui import message_box
message_box.information = lambda *a, **k: 0
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
case = sys.argv[1]

from dopeiptv import i18n
from dopeiptv.core import profiles
from dopeiptv.core.profiles import ProfileSettings
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw, profiles_ui
i18n.set_language("en")
mw.launch_player = lambda *a, **k: None
played = []
mw.MainWindow.play_live_channel = lambda self, it: played.append(it.get("stream_id"))


class Client(DemoClient):
    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"},
                {"category_id": "2", "category_name": "Late night"},
                {"category_id": "3", "category_name": "XXX"}]

    def live_streams(self, category_id=None):
        rows = [{"stream_id": i + 1, "num": i + 1, "name": f"Chan {i + 1}", "stream_icon": "",
                 "category_id": "1" if i < 3 else ("2" if i < 6 else "3"),
                 "epg_channel_id": ""} for i in range(9)]
        return [r for r in rows if category_id in (None, r["category_id"])]


path = os.path.join(tmp, "s.ini")
old = QSettings(path, QSettings.Format.IniFormat)
pid = PlaylistStore(old).add({"name": "Min IPTV", "server": "http://example.invalid",
                              "username": "u", "password": "p"})["id"]
last = {"2": 2, "5": 5, "8": 8}.get(case.split(":")[-1], 2)
cat = "1" if last <= 3 else ("2" if last <= 6 else "3")
old.setValue(f"history_{pid}", json.dumps([
    {"_key": last, "_kind": "live", "stream_id": last, "category_id": cat, "name": f"Chan {last}",
     "_url": "http://x/1", "_watched_at": "2026-11-01T21:00:00"},
    {"_key": 1, "_kind": "live", "stream_id": 1, "category_id": "1", "name": "Chan 1",
     "_url": "http://x/1", "_watched_at": "2026-11-01T20:00:00"}]))
old.setValue("tv_inputs", "false")
old.setValue("check_updates", "false")
if case.startswith("hidden"):
    old.setValue(f"channel_overrides_{pid}", json.dumps({"live": {str(last): {"hidden": True}}}))
if case.startswith("locked"):
    old.setValue(f"category_overrides_{pid}", json.dumps({"live": {cat: {"locked": True}}}))
if case != "off":
    old.setValue("start_last_channel", "true")
old.sync()
del old

s = ProfileSettings(path, QSettings.Format.IniFormat)
store = profiles.start(s, [], "Me")
me = store.active()
if case.startswith("kids") or case.startswith("picker"):
    kid = store.add("Lisa", colour="pink", kids=True)
    kid_s = profiles.pinned(s, kid.id)
    kid_s.setValue(f"history_{pid}", json.dumps([
        {"_key": last, "_kind": "live", "stream_id": last, "category_id": cat,
         "name": f"Chan {last}", "_url": "http://x/1", "_watched_at": "2026-11-01T21:00:00"}]))
    kid_s.setValue("start_last_channel", "true")
    if case.startswith("kids"):
        store.set_ask_at_start(False)
        store.set_active(kid.id)
        profiles.activate(kid.id)
s.sync()

w = mw.MainWindow(Client(), s, PlaylistStore(s))
if case == "command":
    w._start_channel_skip("the app was started with a command")      # as app.py does
w.resize(1280, 800)
w.show()
if case == "handed":
    w.handle_command({"channel": "Chan 3"})


def pump(secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        w.pool.waitForDone(5)
        time.sleep(0.01)


pump(3.0)
out = {"before": list(played), "over": w._sc_over}
if case.startswith("picker"):
    w._profile_pick(me.id if case == "picker" else kid.id)
    pump(2.5)
out["played"] = played
print(json.dumps(out))
sys.stdout.flush()
os._exit(0)
"""


def _run(case: str) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=_REPO_ROOT)
    p = subprocess.run([sys.executable, "-c", _CHILD, case], capture_output=True, text=True,
                       timeout=150, cwd=_REPO_ROOT, env=env)
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("case, want", [
    ("on", [2]),                    # the newest live channel of History, not an older one
    ("off", []),                    # off by default
    ("hidden", []),                 # hidden by hand: nothing, and no older one instead
    ("locked:5", []),               # of a locked category, the lock shut
    ("kids:8", []),                 # a kids profile: its category is for adults
    ("kids:2", [2]),                # ...one it may see plays as for anyone
    ("command", []),                # the app was started with a command
    ("handed", [3]),                # a command handed over plays, the last channel not
])
def test_on_the_real_window(case, want):
    got = _run(case)
    assert got["played"] == want, got


def test_on_the_real_window_it_waits_for_who_is_watching():
    got = _run("picker")
    assert got["before"] == [] and got["over"] == "", "nothing while the picker is up"
    assert got["played"] == [2], "the profile picked, its own last channel"
    got = _run("picker-kid")
    assert got["before"] == [] and got["played"] == [2]
