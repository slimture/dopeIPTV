"""What a playback leaves behind must not start it again by itself: Stop
pressed while the app's reconnect waits (its timer, a backup address asked
for), another source's channel (failover, the Search page) kept out of
Recently viewed through a reconnect too, and a profile switch - what the
profile before played last is not the next one's to bring back (Play after
a Stop, Last channel, a reconnect under way)."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

from dopeiptv.ui.main_window import MainWindow

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _host(last: dict, **more) -> tuple[SimpleNamespace, list]:
    calls: list = []
    h = SimpleNamespace(
        _diag_shown=False, _playing_catchup=False, _last_playback=last,
        _health_retry=lambda: None, client=None,
        play_live_channel=lambda it: calls.append(("channel", it)),
        _start_playback=lambda *a, **k: calls.append((a, k)), **more)
    return h, calls


def _stopped_host(last: dict) -> tuple[SimpleNamespace, list]:
    h, calls = _host(
        last, _save_resume_position=lambda: None, _maybe_auto_mark_watched=lambda: None,
        _failover_stopped=lambda: None, _sync_player_buttons=lambda: None,
        _apply_play_icon=lambda: None, setWindowTitle=lambda t: None, _base_title="",
        _source_status_stopped=lambda: None, _sp_refresh_soon=lambda: None,
        listw=SimpleNamespace(viewport=lambda: SimpleNamespace(update=lambda: None)))
    return h, calls


def test_stop_during_a_reconnect_wait_is_not_undone_by_its_timer():
    item = {"stream_id": 7, "name": "Seven"}
    h, calls = _stopped_host({"url": "http://h/live/7.ts", "title": "Seven", "key": 7,
                              "kind": "live", "item": item})
    # The stream dropped, the retry is armed (QTimer.singleShot) - and Stop.
    MainWindow._on_player_stopped(h)
    MainWindow._retry_last_stream(h)
    assert calls == []


def test_stop_while_a_portals_link_is_asked_for_is_not_undone_by_the_link(monkeypatch):
    from dopeiptv.ui import main_window as mw

    class Portal:
        slow_links = True

        def live_url(self, sid, fmt):
            return ""

        def link_ready(self, name, sid):
            return False

    answers: list = []
    monkeypatch.setattr(mw, "run_async", lambda pool, work, done, fail, *a: answers.append(done))
    h, calls = _stopped_host({"url": "http://h/1", "title": "One", "key": 1, "kind": "live",
                              "item": None})
    h.client, h.pool, h._set_status = Portal(), None, lambda *a, **k: None
    assert MainWindow._links_later(h, lambda: h.client.live_url(7, "ts"),
                                   lambda: calls.append("again"))
    MainWindow._on_player_stopped(h)
    answers[0]("http://portal/link")
    assert calls == []
    # Not stopped: the link's answer plays the channel, as before.
    assert MainWindow._links_later(h, lambda: h.client.live_url(7, "ts"),
                                   lambda: calls.append("again"))
    answers[1]("http://portal/link")
    assert calls == ["again"]


def test_a_reconnect_keeps_another_sources_channel_out_of_recently_viewed():
    last = {"url": "http://other/live/9.ts", "title": "Nine · Other", "key": "failover:b:9",
            "kind": "live", "item": None, "own_buffer": True}
    h, calls = _host(last, _failover_row_for=lambda key: {"stream_id": 9}
                     if key == "failover:b:9" else None)
    MainWindow._retry_last_stream(h)
    (args, kw), = calls
    assert args[0] == "http://other/live/9.ts" and kw == {"record": False}
    # An address replayed from History is kept there, as it always was.
    h, calls = _host(dict(last, key="k"), _failover_row_for=lambda key: None)
    MainWindow._retry_last_stream(h)
    assert calls[0][1] == {}


_CHILD = r"""
import faulthandler, os, tempfile
faulthandler.dump_traceback_later(200, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)

from dopeiptv import i18n
from dopeiptv.core import profiles
from dopeiptv.core.profiles import ProfileSettings
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw, theme

i18n.set_language("en")
mw.launch_player = lambda *a, **k: None
s = ProfileSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
reg = profiles.start(s, [], "Me")
kid = reg.add("Lisa", colour="pink", kids=True)
s.sync()
theme.apply_theme(s)
w = mw.MainWindow(DemoClient(), s, PlaylistStore(s))
w.show()
for _ in range(20):
    app.processEvents()


class Btn:
    clicks = 0

    def click(self):
        Btn.clicks += 1
        w._on_player_stopped()


class Player:
    current_url = None          # dropped: the app's reconnect waits
    stop_btn = Btn()

    def __getattr__(self, name):
        return lambda *a, **k: 0


w.player = Player()
w._sync_player_buttons = w._apply_play_icon = lambda: None    # the stand-in has no buttons
started = []
w._start_playback = lambda *a, **k: started.append(a)
w.play_live_channel = lambda it: started.append(it)
late = {"stream_id": 5, "name": "Late night"}
w._last_playback = {"url": "http://x/live/5.ts", "title": "Late night", "icon_url": None,
                    "key": 5, "kind": "live", "item": late}
w._prev_live_item = {"stream_id": 6, "name": "Later still"}
w._playing_key, w._playing_group, w._playing_item = 5, "live", late
w._slow_reconnect_key = 5

assert w.switch_profile(kid.id)
# The channel that waited for its reconnect was stopped, as Stop does...
assert Btn.clicks == 1 and w._playing_item is None
# ...and nothing brings what the grown-up watched into the kid's profile:
w._retry_last_stream()          # the reconnect's timer, armed before
w._slow_reconnect()
w._resume_last()                # Play after a Stop (the button, Space, a media key)
w._typing = lambda: False
w._last_channel()               # Backspace
assert started == [], started
print("LEFT_BEHIND_OK", flush=True)
os._exit(0)
"""


def test_a_profile_switch_leaves_nothing_of_the_last_playback_to_come_back():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    assert "LEFT_BEHIND_OK" in out.stdout, out.stdout[-3000:] + out.stderr[-4000:]
