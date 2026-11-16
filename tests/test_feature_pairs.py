"""The features against each other (docs/MAINTAINING.md, "Every feature works
with the others"): casting next to the rewind buffer in one count of the
account's connections, Watch together through a failover, private viewing
and every reporter - during it and for the rest of a playback that was
private - the statistics and Discord on a channel switch, and the phone
remote, Home Assistant, casting and multiview meeting the VPN guard, the
connection limit and a box's tuners (the transponder check) the way the
player does."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dopeiptv.core import connections, events, privacy
from dopeiptv.i18n import tr

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def allKeys(self):  # noqa: N802 - Qt's name
        return list(self)


@pytest.fixture
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _not_private():
    privacy.set_private(False)
    privacy.playback_stopped()
    yield
    privacy.set_private(False)
    privacy.playback_stopped()


# -- one count: casting and the rewind buffer ------------------------------------------

class _Dvr:
    def __init__(self, recording=False):
        self.streaming, self.recording = True, recording


class _Player:
    def __init__(self, url=None, dvr=None):
        self.current_url, self._dvr = url, dvr
        self._dvr_pool = SimpleNamespace(sessions=[dvr] if dvr is not None else [])


def _counting(limit=None, player=None, casting=None, jobs=()):
    from dopeiptv.ui.mw_connections import _ConnectionsMixin
    from dopeiptv.ui.mw_recording import _RecordingMixin

    class W(_ConnectionsMixin, _RecordingMixin):
        def __init__(self):
            self.client = SimpleNamespace(max_connections=limit)
            self.player = player
            self.rec = SimpleNamespace(jobs=list(jobs))
            self._multiview_win = None
            self.playlist_store = SimpleNamespace(active_id="pl1")
            self.settings = _S(mv_stop_docked="true")
            self._failover_source = None
            self.cast = SimpleNamespace(active=object() if casting else None)
            self._cast_ctx = {"row_source": casting} if casting else None
            self.asked: list[str] = []
            self.answer = 1

        def _choice_dialog(self, title, message, options, dont_ask_setting=None):
            self.asked.append(message)
            return self.answer
    return W()


def test_a_cast_counts_once_beside_a_buffer_a_recording_keeps():
    # Handed over to the TV while a recording reads the channel's buffer:
    # the buffer goes on, and the TV (or Kodi, or the converter here)
    # fetches the stream too.
    kept = _Dvr(recording=True)
    w = _counting(player=_Player(None, kept), casting="http://h.example.com/live/u/p/1.ts")
    assert sorted(u.kind for u in w._conn_uses()) == ["buffer", "cast"]
    assert connections.own_count(w._conn_uses(), "pl1") == 2
    # A file of this computer's cast to a DLNA TV is no connection.
    local = _counting(casting="/home/me/film.mkv")
    assert local._conn_uses() == []
    # A cast ends as the docked player starts: not one of the rest there.
    w.client.max_connections = 2
    assert w._one_more_over(free_player=True) is None


def test_a_cast_over_the_limit_is_asked_about_and_said_by_whom():
    kept = _Dvr(recording=True)
    w = _counting(limit=1, player=_Player(None, kept))
    assert not w._conn_ok_cast("http://h.example.com/live/u/p/2.m3u8")
    assert tr("conn_what_cast") in w.asked[0]
    assert tr("conn_over", max=1, n=1) in w.asked[0] and tr("conn_k_buffer") in w.asked[0]
    w.answer = 0                                          # Go ahead anyway
    assert w._conn_ok_cast("http://h.example.com/live/u/p/2.m3u8")
    # Another cast in its place gives way; a file asks nothing.
    w2 = _counting(limit=1, casting="http://h.example.com/live/u/p/1.ts")
    assert w2._conn_ok_cast("http://h.example.com/live/u/p/2.m3u8") and not w2.asked
    assert w2._conn_ok_cast("/home/me/film.mkv") and not w2.asked
    # Multiview next to a running cast: the cast is in the count, by name.
    w2.settings["mv_stop_docked"] = "false"
    assert not w2._conn_ok_multiview(None, "http://h.example.com/live/u/p/3.ts")
    assert tr("conn_k_cast") in w2.asked[-1]


def test_the_rewind_buffer_beside_a_cast_is_asked_about_unless_there_is_room():
    # The buffer kept recording beside a live cast (Settings > Casting) is a
    # connection of its own next to the TV's: within a known limit with room
    # it goes ahead; over one, or with none known, it is asked first and a
    # No is kept to - never opened silently.
    cast = "http://h.example.com/live/u/p/1.ts"
    w = _counting(player=_Player(cast, _Dvr()))
    assert not w._conn_ok_cast_buffer(cast)              # unknown: asked, No
    assert tr("conn_what_cast_buffer") in w.asked[-1]
    assert tr("conn_limit_unknown") in w.asked[-1]
    w.answer = 0                                          # Go ahead anyway
    assert w._conn_ok_cast_buffer(cast)
    w.asked.clear()
    w.answer = 1
    w.client.max_connections = 1                          # the TV takes the one
    assert not w._conn_ok_cast_buffer(cast)
    assert tr("conn_over", max=1, n=1) in w.asked[-1]
    assert tr("conn_k_cast") in w.asked[-1]
    w.asked.clear()
    w.client.max_connections = 2            # the player's own one handed over
    assert w._conn_ok_cast_buffer(cast) and not w.asked
    w.rec.jobs.append({"status": "recording", "source": "pl1",
                       "url": "http://h.example.com/live/u/p/9.ts"})
    assert not w._conn_ok_cast_buffer(cast) and len(w.asked) == 1
    assert tr("conn_k_recording") in w.asked[-1]


# -- Watch together through a failover -----------------------------------------------

def test_watch_together_stays_on_the_channel_through_a_failover():
    from dopeiptv.core import nerd
    from dopeiptv.services import together as matching
    from dopeiptv.ui.mw_failover import _FailoverMixin
    from dopeiptv.ui.mw_together import _TogetherMixin

    here = {"name": "SVT1 HD", "stream_id": 1, "epg_channel_id": "svt1.se"}
    room_media = matching.describe("live", here)

    class W(_FailoverMixin, _TogetherMixin):
        def __init__(self):
            self._together_room = SimpleNamespace(state=SimpleNamespace(media=room_media))
            self.told: list = []
            self._last_playback: dict = {}
            self.player = SimpleNamespace(current_url=None)
            self.playlist_store = SimpleNamespace(active=lambda: {"id": "pl1"}, active_id="pl1")

        def _together_on_local(self, media):
            self.told.append(media)

        def _start_playback(self, url, title, icon, key, kind, record=True, item=None, **_kw):
            self._failover_leave(url, key)
            self._last_playback = {"url": url, "key": key, "kind": kind, "item": item}
            self.player.current_url = url
            self._together_started(key, title, item, kind, False)

        def _show_toast(self, *_a):
            pass

    w = W()
    try:
        other = {"id": "pl2", "name": "Backup"}
        row = {"name": "SVT 1", "stream_id": 77, "epg_channel_id": "svt1.se"}
        w._failover_play(other, row, "http://b.example.com/live/7.ts", "SVT1 HD")
        # The same channel from the other source: the room stays on it.
        assert matching.same(room_media, w.told[-1])
        assert matching.same(room_media, w._together_now_playing())
        # Another channel after it: the failover row is gone with it.
        w._start_playback("http://a.example.com/live/2.ts", "Other", None, 2, "live",
                          item={"name": "Other", "stream_id": 2})
        assert w._failover_row_for("failover:pl2:77") is None
        assert not matching.same(room_media, w.told[-1])
    finally:
        nerd.set_source(None)


# -- private viewing and every reporter --------------------------------------------------

class _FakeDispatcher:
    active = True

    def __init__(self):
        self.cfg = SimpleNamespace(commands=False)
        self.sent: list[dict] = []
        self.states: list[dict] = []

    def emit(self, event, fields=None, private=False, state=None, now=None):
        self.sent.append(events.payload(event, fields, private))
        if state is not None:
            self.set_state(*state, private=private)

    def set_state(self, state, fields=None, private=False, now=None):
        self.states.append(events.state_payload(state, fields, private))


class _MsClient:
    reports_playback = True
    REPORT_EVERY = 10.0

    def __init__(self):
        self.sent: list[tuple] = []

    def report_target(self, key, kind, item, url):
        return {"id": f"ms-{key}", "kind": kind}

    def report(self, event, target, pos, dur, paused=False):
        self.sent.append((event, target["id"]))


@pytest.fixture
def reporters(app, monkeypatch):
    from PyQt6.QtCore import QObject

    from dopeiptv.core import discord_rpc
    from dopeiptv.ui import mw_stats
    from dopeiptv.ui.mw_discord import _DiscordMixin
    from dopeiptv.ui.mw_events import _EventsMixin
    from dopeiptv.ui.mw_media import _MediaServerMixin
    from dopeiptv.ui.mw_privacy import _PrivacyMixin
    from dopeiptv.ui.mw_together import _TogetherMixin
    from tests.test_integrations_ui import FakePresence

    clock = [1000.0]
    monkeypatch.setattr(mw_stats.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(discord_rpc, "DISCORD_APP_ID", "99")
    monkeypatch.setattr(discord_rpc, "Presence", FakePresence)
    FakePresence.made = []

    class W(_PrivacyMixin, mw_stats._StatsMixin, _DiscordMixin, _MediaServerMixin,
            _EventsMixin, _TogetherMixin, QObject):
        def __init__(self):
            QObject.__init__(self)
            self.settings = _S(discord_presence="true")
            self.player = SimpleNamespace(current_url=None, playback_position=lambda: 0.0,
                                          playback_duration=lambda: 0.0)
            self.playlist_store = SimpleNamespace(active_id="pl1", active=lambda: {"name": "Mine"})
            self.xmltv = SimpleNamespace(current_programme=lambda it: {"title": "News"})
            self.series_ctx = None
            self._last_playback: dict = {}
            self.client = _MsClient()
            self._events = _FakeDispatcher()
            self._together_room = None
            self._playing_catchup = False

        def _show_toast(self, *_a):
            pass

        def start(self, key, name):
            """What _start_playback does for a live channel that plays."""
            item = {"name": name, "stream_id": key}
            url = f"http://h.example.com/live/{key}.ts"
            self._last_playback = {"url": url, "key": key, "kind": "live", "item": item}
            privacy.playback_started("live", key)
            self.player.current_url = url
            self._events_started(key, name, item, "live", False)
            self._stats_begin(key, name, item, "live", False)
            self._discord_started(key, name, item, "live", False)
            self._ms_started(url, key, "live", item)

        def counted(self) -> float:
            return sum(r.secs for r in self._stats_store().rows())

    w = W()
    yield w, clock
    for t in (w._stats_timer, w._ms_timer):
        if t is not None:
            t.stop()


def test_private_viewing_turned_on_mid_play_and_the_rest_of_that_playback(reporters):
    from tests.test_integrations_ui import FakePresence
    w, clock = reporters
    w.start(1, "SVT1")
    (presence,) = FakePresence.made
    assert presence.shown[-1]["details"] == "Watching SVT1"
    assert w.client.sent == [("start", "ms-1")]
    assert w._events.sent[-1]["event"] == "playback_started" and w._events.sent[-1]["title"] == "SVT1"
    clock[0] += 60
    w.set_private_viewing(True)
    # What played before counts; nothing after. The server hears where it got
    # to, Discord's status goes, the state says "private" at once.
    assert w.counted() == pytest.approx(60)
    assert w.client.sent[-1] == ("stop", "ms-1")
    assert presence.shown[-1] is None
    assert w._events.states[-1].get("private") is True and "title" not in w._events.states[-1]
    clock[0] += 60
    w._stats_tick()
    w.set_private_viewing(False)
    # The same playback going on - a reconnect - stays private to its end.
    w.start(1, "SVT1")
    clock[0] += 60
    w._stats_tick()
    assert w.counted() == pytest.approx(60)
    assert w.client.sent == [("start", "ms-1"), ("stop", "ms-1")]
    assert presence.shown[-1] is None
    assert w._together_now_playing() is None
    w._events_paused(True)
    paused = w._events.sent[-1]
    assert paused["event"] == "playback_paused" and paused.get("private") is True
    assert "title" not in paused and "channel" not in paused
    # The next channel chosen is reported again - never naming the private one.
    w.start(2, "SVT2")
    assert presence.shown[-1]["details"] == "Watching SVT2"
    assert w.client.sent[-1] == ("start", "ms-2")
    changed = w._events.sent[-1]
    assert changed["event"] == "channel_changed" and changed["channel"] == "SVT2"
    assert "previous" not in changed and not changed.get("private")
    clock[0] += 30
    w._stats_flush()
    assert w.counted() == pytest.approx(90)


def test_a_playback_started_privately_stays_private_after_and_a_stop_ends_that(reporters):
    w, clock = reporters
    privacy.set_private(True)
    w.start(5, "Kanal 5")
    privacy.set_private(False)
    assert privacy.playback_private("live", 5)
    clock[0] += 60
    w._stats_tick()
    w._events_stopped()
    assert w.counted() == 0 and w.client.sent == []
    assert all(m.get("private") for m in w._events.sent)
    assert all("title" not in m for m in w._events.sent)
    # Stop, then the same channel chosen again: a new playback, reported.
    privacy.playback_stopped()
    assert not privacy.playback_private("live", 5)
    w.start(5, "Kanal 5")
    assert w.client.sent == [("start", "ms-5")]
    assert w._events.sent[-1]["title"] == "Kanal 5"


# -- the end hooks on a channel switch -----------------------------------------------------

def test_statistics_and_discord_move_on_with_a_channel_switch(reporters):
    from tests.test_integrations_ui import FakePresence
    w, clock = reporters
    w.start(1, "SVT1")
    clock[0] += 50
    w.start(2, "SVT2")                    # a zap: no Stop in between
    clock[0] += 40
    w._stats_end()                        # Stop
    names = {t["n"]: sum(t["h"].values()) for t in w._stats_store().titles.values()}
    assert names == {"SVT1": pytest.approx(50), "SVT2": pytest.approx(40)}
    (presence,) = FakePresence.made
    assert [a["details"] for a in presence.shown] == ["Watching SVT1", "Watching SVT2"]
    w._discord_stopped()
    assert presence.shown[-1] is None
    assert w._stats_now is None


def test_a_zap_on_a_cast_moves_the_reporters_on_once(reporters):
    """A cast is what is watched: its start, each zap on it and its end
    reach the events and Discord once each, as a local zap does - and the
    statistics count the player only, as they say."""
    from dopeiptv.ui.mw_cast import _CastMixin
    from tests.test_integrations_ui import FakePresence
    w, clock = reporters

    def on_tv(key, name):
        return {"key": key, "kind": "live", "title": name,
                "item": {"name": name, "stream_id": key}}

    _CastMixin._cast_began(w, on_tv(1, "SVT1"))
    clock[0] += 50
    _CastMixin._cast_began(w, on_tv(2, "SVT2"))       # a zap on the cast
    got = [(m["event"], m.get("channel"), m.get("previous")) for m in w._events.sent]
    assert got == [("playback_started", "SVT1", None),
                   ("playback_started", "SVT2", None),
                   ("channel_changed", "SVT2", "SVT1")], got
    (presence,) = FakePresence.made
    assert [a["details"] for a in presence.shown] == ["Watching SVT1", "Watching SVT2"]
    _CastMixin._cast_hooks_end(w)                     # Stop casting
    _CastMixin._cast_hooks_end(w)                     # and the strip again
    assert [m["event"] for m in w._events.sent][3:] == ["playback_stopped"]
    assert presence.shown[-1] is None and w.counted() == 0
    # Private viewing: the cast is private like a play here.
    privacy.set_private(True)
    _CastMixin._cast_began(w, on_tv(3, "Kanal 5"))
    assert privacy.playback_private("live", 3)
    assert w._events.sent[-1].get("private") and "title" not in w._events.sent[-1]
    assert presence.shown[-1] is None
    _CastMixin._cast_hooks_end(w)


# -- the window: phone remote, Home Assistant, casting and multiview -----------------------

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from dopeiptv.core import nerd
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw, mw_cast

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.Format.IniFormat)
w = mw.MainWindow(DemoClient(), settings)
w.show()
app.processEvents()

toasts, asked, casts = [], [], []
w._show_toast = lambda text, ms=0: toasts.append(text)
w._choice_dialog = lambda title, message, options, dont_ask_setting=None: (asked.append(message), len(options) - 1)[1]
mw_cast.CastDialog = lambda *a, **k: SimpleNamespace(exec=lambda: casts.append(a[1]))


def spin(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        w.pool.waitForDone(10)
        if cond():
            return True
    return False


def playing():
    return bool(w.player is not None and w.player.current_url)


rows = w.client.live_streams(None)
row = rows[0]

class D:
    active = True
    cfg = SimpleNamespace(commands=True)
    def emit(self, *a, **k): pass
    def set_state(self, *a, **k): pass
    def set_favorites(self, *a, **k): pass

w._events = D()
w._events_favs = {row["name"]: row}

# Nerd's VPN guard says no: every way in says it the same, and nothing starts.
real_blocks = nerd.blocks_stream
nerd.blocks_stream = lambda url, kind=None: not nerd.is_own_file(url)
blocked = tr("nerd_vpn_blocked")
w._phone_do("_play", {"row": row})
assert toasts[-1:] == [blocked] and not playing(), ("phone", toasts)
assert w._phone_do("state", {}).get("note") == blocked, "the phone's page says it too"
toasts.clear()
w._events_command("favorite", row["name"])
assert toasts[-1:] == [blocked] and not playing(), ("mqtt favorite", toasts)
toasts.clear()
w._events_command("play", row["name"])
assert spin(lambda: toasts), "mqtt play by name"
assert toasts[-1] == blocked and not playing()
toasts.clear()
w._open_cast_dialog(dict(row, _kind="live"))
assert toasts[-1:] == [blocked] and not casts, ("cast", toasts, casts)
toasts.clear()
if w._multiview_available():
    w.add_to_multiview(w.client.live_url(row["stream_id"]), row["name"])
    assert toasts[-1:] == [blocked] and w._multiview_win is None, ("multiview", toasts)
nerd.blocks_stream = real_blocks

# A recording over its own connection and an account of one: the phone and
# Home Assistant ask the same question the window does, with the count.
w.client.max_connections = 1
job = {"id": 999, "status": "recording", "url": "https://rec.example.com/live/9.ts",
       "title": "Rec", "stop": None}
w.rec.jobs.append(job)
over = tr("conn_over", max=1, n=1)
w._phone_do("_play", {"row": row})
assert asked and over in asked[-1] and not playing(), ("phone limit", asked)
note = w._phone_do("state", {}).get("note") or ""
assert over in note and tr("phone_answer_there") in note, note
asked.clear()
w._events_command("favorite", row["name"])
assert asked and over in asked[-1] and not playing(), ("mqtt limit", asked)
asked.clear()
w._open_cast_dialog(dict(row, _kind="live"))
assert asked and tr("conn_what_cast") in asked[-1] and over in asked[-1] and not casts, ("cast limit", asked)
w.rec.jobs.remove(job)
w.client.max_connections = None
from dopeiptv.core import cert_pin


class S(dict):
    def value(self, key, default=None):
        return self.get(key, default)


nerd.install(S(nerd_enabled="true"))
pinned = {"id": "pinned", "name": "Home box",
          "options": cert_pin.pin_options("tv.example.com", 8443, "ab" * 32)}
store_was = w.playlist_store
w.playlist_store = SimpleNamespace(active=lambda: pinned, active_id="pinned",
                                   playlists=lambda: [pinned])
film = {"name": "Film", "_kind": "movie", "_cast_url": "https://tv.example.com:8443/movie/1.mkv"}
asked.clear()
w._open_cast_dialog(film)
assert asked and tr("cast_pin_body", name="Home box", server="tv.example.com:8443") in asked[-1], asked
assert not casts, "cancelled: not cast"
w._choice_dialog = lambda title, message, options, dont_ask_setting=None: (asked.append(message), 0)[1]
w._open_cast_dialog(film)
assert casts, "cast anyway"
asked.clear()
w._open_cast_dialog(film)
assert not any(tr("cast_pin_title") in a or "tv.example.com" in a for a in asked), "asked once per source"
nerd.install(None)
w.playlist_store = store_was
print("PAIRS_WINDOW_OK")
"""


def test_every_way_in_meets_the_vpn_guard_and_the_connection_limit():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for _attempt in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD], capture_output=True, text=True,
            env=env, cwd=_REPO_ROOT, timeout=180)
        if "PAIRS_WINDOW_OK" in proc.stdout:
            return
    assert "PAIRS_WINDOW_OK" in proc.stdout, (
        f"the ways in did not meet the same checks\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}")


# -- zapping while casting: every way in, the same receiver --------------------------------

_ZAP_CHILD = r"""
import os, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from dopeiptv.core import nerd
from dopeiptv.i18n import tr
from dopeiptv.providers import casting
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw, mw_cast

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.Format.IniFormat)
w = mw.MainWindow(DemoClient(), settings)
w.show()
app.processEvents()


class Rx:
    # One kind of receiver as CastHub sees it: what it is asked, and when.
    def __init__(self, names, kind=""):
        self.names, self.KIND, self.active, self.casts, self.stops = names, kind, None, [], 0
        self.state, self.duration, self.gone, self.hold, self.volumes = "PLAYING/NONE", 0.0, False, None, []
        self.bridge = SimpleNamespace(cap=0, hls=False)
    def knows(self, name): return name in self.names
    def entries(self): return list(self.names)
    def scan(self): return list(self.names)
    def cast(self, name, url, title, *a, **k):
        if self.hold is not None:
            self.hold.wait(10)
        self.casts.append((name, url, title))
        self.active = name
        return name
    def stop(self): self.stops += 1; self.active = None
    def shutdown(self): pass
    def position(self): return 0.0
    def bridged(self): return False
    def volume(self): return 0.5, False
    def set_volume(self, v): self.volumes.append(v)
    def set_muted(self, m): self.volumes.append(("mute", m))
    def show_window(self, *_a): self.shown = getattr(self, "shown", 0) + 1


class Menu:
    # The strip's menu, read rather than shown.
    made = []
    def __init__(self, *a): self.acts = []; Menu.made.append(self)
    def addAction(self, label, slot=None):
        self.acts.append((label, slot))
        return SimpleNamespace(setEnabled=lambda on: None, setCheckable=lambda on: None,
                               setChecked=lambda on: None,
                               triggered=SimpleNamespace(connect=lambda f: None))
    def addMenu(self, label): return Menu()
    def addSeparator(self): pass
    def exec(self, *a): pass


def strip_menu():
    Menu.made.clear()
    real, mw_cast.QMenu = mw_cast.QMenu, Menu
    try:
        w._cast_tracks_menu()
    finally:
        mw_cast.QMenu = real
    return dict(Menu.made[0].acts)


cc, tv, kodi = Rx(["Kitchen"]), Rx(["TV (DLNA)"]), Rx(["Den (Kodi)"])
ap = Rx(["AirPlay (Apple TV)"], "airplay")
w.cast = casting.CastHub(cc, tv, kodi)
w.cast.add_receiver(ap)
picked = ["Kitchen"]


def dialog(win, url, title, *a, **k):
    # What the cast dialog does once a device is picked and the cast plays.
    def run():
        win.stop_local_playback_for_cast()
        win._cast_ctx.update(url=url, source=k.get("source"))
        win.cast.cast(picked[0], url, title)
        win.show_cast_strip(picked[0], title)
    return SimpleNamespace(exec=run)


mw_cast.CastDialog = dialog
sent, toasts, asked = [], [], []


class D:
    active = True
    cfg = SimpleNamespace(commands=True)
    def emit(self, event, fields=None, private=False, state=None, now=None):
        sent.append((event, (fields or {}).get("channel"), (fields or {}).get("previous")))
    def set_state(self, *a, **k): pass
    def set_favorites(self, *a, **k): pass


w._events = D()
w._show_toast = lambda text, ms=0: toasts.append(text)
w._choice_dialog = lambda title, message, options, dont_ask_setting=None: (asked.append(message), len(options) - 1)[1]


def spin(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        w.pool.waitForDone(10)
        if cond():
            return True
    return False


def playing():
    return bool(w.player is not None and w.player.current_url)


def zapped(rx, n, row, how):
    assert spin(lambda: len(rx.casts) >= n), (how, rx.casts)
    assert len(rx.casts) == n, (how, rx.casts)
    name, url, title = rx.casts[-1]
    assert name == rx.names[0] and title == row["name"], (how, rx.casts[-1])
    assert url == w.client.live_url(row["stream_id"], "m3u8"), (how, url)
    assert spin(lambda: w.cast_bar_title.text() == row["name"]), (how, w.cast_bar_title.text())
    assert w._cast_device == rx.names[0] and not playing(), how
    assert w._last_playback["key"] == row["stream_id"], how


rows = w.client.live_streams(None)
a, b, c, d, e = rows[:5]

# A channel cast from the dialog: the player hands over; the cast is told.
w._open_cast_dialog(dict(a, _kind="live"))
assert cc.casts == [("Kitchen", w.client.live_url(a["stream_id"], "m3u8"), a["name"])]
assert not w.cast_bar.isHidden() and not playing()
assert sent == [("playback_started", a["name"], None)], sent

# The guide, Home, a reminder, the phone's list: play_live_channel. The same
# Chromecast; the old channel ends and the new starts, once each.
w.play_live_channel(b)
zapped(cc, 2, b, "play_live_channel")
assert sent[1:] == [("playback_started", b["name"], None),
                    ("channel_changed", b["name"], a["name"])], sent
assert not cc.stops and (w.player is None or not w.player.isVisible())
# The same channel again changes nothing.
w.play_live_channel(b)
assert not spin(lambda: len(cc.casts) > 2, 0.5) and len(sent) == 3

# The list: a double-click / Enter (play), and channel up/down (zap).
w.switch_mode("live")
assert spin(lambda: w.list_model.rowCount() >= 5), w.list_model.rowCount()
order = [w.list_model.item_at(i) for i in range(w.list_model.rowCount())]
i = next(k for k, r in enumerate(order) if r["stream_id"] == c["stream_id"])
nxt = order[(i + 1) % len(order)]
w.listw.setCurrentIndex(w.list_model.index(i))
w.play()
zapped(cc, 3, c, "the list's play")
w._zap(1)
zapped(cc, 4, nxt, "channel up")
w._zap(-1)
zapped(cc, 5, c, "channel down")
w._last_channel()
zapped(cc, 6, nxt, "the last channel")

# A number: typed (whatever the list shows), the source's numbers.
target = e if e["stream_id"] != nxt["stream_id"] else a
w.switch_mode("vod")
assert w._live_playing(), "a channel on the cast takes a typed number"
for digit in str(target["num"]):
    w._channel_digit(digit, from_player=True)
w._channel_jump()
zapped(cc, 7, target, "a typed number")
w._play_channel_number(b["num"])
zapped(cc, 8, b, "a number asked for")

# The phone remote and Home Assistant.
w._phone_do("_play", {"row": a})
zapped(cc, 9, a, "the phone's play")
assert w._phone_do("next", {}) == {"ok": True}
zapped(cc, 10, b, "the phone's next")
w._events_command("play", d["name"])
zapped(cc, 11, d, "Home Assistant's play")
w._events_command("channel_down", None)
zapped(cc, 12, c, "Home Assistant's channel down")

# The media keys: next and previous, from the channel on the TV.
if w.player is not None:
    snap = w.mpris_snapshot()
    assert snap["status"] == "Stopped" and snap["can_next"] and snap["can_prev"], snap
    w.mpris_command("next")
    zapped(cc, 13, d, "the media key next")
    w.mpris_command("previous")
    zapped(cc, 14, c, "the media key previous")
n = len(cc.casts)

# Picked fast, one after another: one at a time, the newest last.
cc.hold = threading.Event()
w.play_live_channel(a)
w.play_live_channel(b)
w.play_live_channel(e)
assert w.cast_bar_title.text() == e["name"]
cc.hold.set()
assert spin(lambda: len(cc.casts) == n + 2), cc.casts
assert [t for _n, _u, t in cc.casts[n:]] == [a["name"], e["name"]], cc.casts[n:]
cc.hold = None
n = len(cc.casts)

# The VPN guard and the connection limit still say no - and the cast goes on.
real_blocks = nerd.blocks_stream
nerd.blocks_stream = lambda url, kind=None: not nerd.is_own_file(url)
w.play_live_channel(a)
assert toasts[-1:] == [tr("nerd_vpn_blocked")], toasts
nerd.blocks_stream = real_blocks
w.client.max_connections = 1
job = {"id": 999, "status": "recording", "url": "https://rec.example.com/live/9.ts",
       "title": "Rec", "stop": None}
w.rec.jobs.append(job)
w._multi_stream_ok = True          # the recording's own question answered
w.play_live_channel(a)
assert asked and tr("conn_what_cast") in asked[-1], asked
assert tr("conn_over", max=1, n=1) in asked[-1], asked
w.rec.jobs.remove(job)
w._multi_stream_ok = False
w.client.max_connections = None
assert not spin(lambda: len(cc.casts) > n, 0.5) and cc.active == "Kitchen"
assert w.cast_bar_title.text() == e["name"] and not playing()

# A DLNA TV, a Kodi and an Apple TV: the same, each on its own device.
for rx in (tv, kodi, ap):
    picked[0] = rx.names[0]
    w._open_cast_dialog(dict(a, _kind="live"))
    assert rx.casts[-1][0] == rx.names[0]
    w.play_live_channel(b)
    zapped(rx, 2, b, rx.names[0])
    assert w.cast.active == rx.names[0]
# AirPlay: the Apple TV's remote sets its volume - no slider, nothing sent.
assert w.cast_bar_vol.isHidden() and w.cast_bar_mute.isHidden()
w._cast_volume(0.3)
w._toggle_cast_mute()
assert not spin(lambda: ap.volumes, 0.5), ap.volumes
# The AirPlay window, closed, is one click from the strip's own menu.
acts = strip_menu()
assert tr("airplay_show_window") in acts and tr("airplay_menu_note") in acts, acts
acts[tr("airplay_show_window")]()
assert getattr(ap, "shown", 0) == 1
# ...and so is the strip's own AirPlay button, shown for AirPlay alone.
assert not w.cast_bar_airplay.isHidden()
assert w.cast_bar_airplay.toolTip() == tr("airplay_show_window")
w.cast_bar_airplay.click()
assert ap.shown == 2

# Stop casting is the one way off the cast: told once, and the next
# channel plays here again.
before = len(sent)
w.cast_bar_stop.click()
assert spin(lambda: ap.stops == 1) and w._cast_device is None
assert [s[0] for s in sent[before:]] == ["playback_stopped"], sent[before:]
assert not w._cast_zap(w.client.live_url(c["stream_id"]), c["name"], None,
                       c["stream_id"], "live", c, False)
# Back on a Chromecast, the slider and the mute are there again.
picked[0] = "Kitchen"
w._open_cast_dialog(dict(a, _kind="live"))
assert not w.cast_bar_vol.isHidden() and not w.cast_bar_mute.isHidden()
w._cast_volume(0.3)
assert spin(lambda: cc.volumes), "a Chromecast's volume is still set"
assert tr("airplay_show_window") not in strip_menu()
assert w.cast_bar_airplay.isHidden()
print("CAST_ZAP_OK")
"""


def test_zapping_while_casting_stays_on_the_same_receiver():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _ZAP_CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    assert "CAST_ZAP_OK" in proc.stdout, (
        f"zapping during a cast\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr[-4000:]!r}")


# -- a film or an episode picked while casting: the same receiver -------------------------

_TITLE_CHILD = _ZAP_CHILD.split("rows = w.client.live_streams(None)")[0] + r"""
import json
from dopeiptv.providers import cast_bridge

looked = []
cast_bridge.probe_tracks = lambda src, exe=None: (looked.append(src), {
    "duration": 5400.0, "audio": [{"index": 0}, {"index": 1}],
    "subtitle": [{"index": 0, "codec": "subrip", "lang": "swe"}]})[1]
casts = []
plain = Rx.cast


def cast(self, name, url, title, *a, **k):
    casts.append((name, url, title, a))
    return plain(self, name, url, title, *a, **k)


Rx.cast = cast
w.client.vod_url = lambda sid, ext=None: "http://p/movie/u/pw/%s.mkv" % sid
w.client.episode_url = lambda eid, ext=None: "http://p/series/u/pw/%s.mkv" % eid
hooks = []
w._trakt_start_for_item = lambda kind, item: hooks.append(("trakt", kind, item.get("name")))
w._simkl_start_for_item = lambda kind, item: hooks.append(("simkl", kind))
w._ms_started = lambda url, key, kind, item, at=0.0: hooks.append(("ms", kind, at))
saved = {"movie:901": 1830.0}
w.resume.saved_position = lambda key, kind: saved.get("%s:%s" % (kind, key), 0.0)
w._ms_resume_at = lambda key, kind, item, pos: pos
w._choice_dialog = lambda title, message, options, dont_ask_setting=None: (asked.append(message), 0)[1]

a = w.client.live_streams(None)[0]
w._open_cast_dialog(dict(a, _kind="live"))
assert w._cast_device == "Kitchen" and not playing()

# A film: to the Chromecast, from where it was left (the resume question
# asked as here), its length told, its scrobble and report begun.
film = {"stream_id": 901, "name": "Film", "container_extension": "mkv", "_kind": "movie"}
w._start_playback(w.client.vod_url(901), "Film", None, 901, "movie", item=film)
assert spin(lambda: len(casts) >= 2), casts
name, url, title, args = casts[-1]
assert (name, url, title) == ("Kitchen", "http://p/movie/u/pw/901.mkv", "Film"), casts[-1]
assert args[3] == 1830.0 and args[4] == 5400.0, args
assert any("30:30" in m for m in asked), asked
assert w._cast_device == "Kitchen" and not playing() and cc.stops == 0
assert w._cast_ctx["kind"] == "movie" and w._last_playback["kind"] == "movie"
assert ("trakt", "movie", "Film") in hooks and ("ms", "movie", 1830.0) in hooks, hooks
assert spin(lambda: w.cast_bar_title.text() == "Film")

# An episode with its remembered sound track and subtitle: through the
# converter, the subtitle's kind looked up.
w.series_ctx = {"series_id": 77, "name": "Show"}
e1 = {"id": 5001, "name": "S01 E01", "container_extension": "mkv"}
e2 = {"id": 5002, "name": "S01 E02", "container_extension": "mkv"}
w.all_items = [e1, e2]
w.settings.setValue("track_prefs", json.dumps({"episode:5001": {"aid": 2, "sid": 1}}))
w._start_playback(w.client.episode_url(5001), "S01 E01", None, 5001, "episode", item=e1)
assert spin(lambda: len(casts) >= 3), casts
name, url, title, args = casts[-1]
assert url == "http://p/series/u/pw/5001.mkv" and args[3] == 0.0, casts[-1]
assert args[1] == {"index": 1}, args
assert args[2] == {"index": 0, "codec": "subrip", "lang": "swe"}, args
assert w._last_playback["ep_queue"] == [e1, e2] and w._last_playback["ep_index"] == 0
assert ("trakt", "episode", "S01 E01") in hooks

# Its end on the TV: the next episode follows there, not here.
w.series_ctx = None
cc.state = "IDLE/FINISHED"
w._show_cast_progress()
assert spin(lambda: len(casts) >= 4), casts
assert casts[-1][1] == "http://p/series/u/pw/5002.mkv" and casts[-1][2] == "S01 E02"
cc.state = "PLAYING/NONE"
assert w._cast_device == "Kitchen" and not playing()
assert w._last_playback["ep_index"] == 1

# On an Apple TV, a title goes as it is (the sound track through the
# bridge), and the receiver is not looked into here.
picked[0] = "AirPlay (Apple TV)"
w._open_cast_dialog(dict(a, _kind="live"))
n = len(looked)
w._start_playback(w.client.vod_url(902), "Film 2", None, 902,
                  "movie", item={"stream_id": 902, "name": "Film 2", "_kind": "movie"})
assert spin(lambda: ap.casts and ap.casts[-1][2] == "Film 2"), ap.casts
assert len(looked) == n and casts[-1][3][2] is None
print("CAST_TITLE_OK")
"""


def test_a_film_or_an_episode_picked_while_casting_goes_to_the_receiver():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _TITLE_CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    assert "CAST_TITLE_OK" in proc.stdout, (
        f"titles during a cast\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr[-4000:]!r}")


def test_a_channel_asked_for_by_name_is_one_the_lists_show():
    """Home Assistant's "play", the remote API and --channel: never one from a
    locked or hidden category - as by number and from the phone."""
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_commands import _CommandsMixin
    from dopeiptv.ui.mw_context import _ContextMenuMixin

    played: list[str] = []
    toasts: list[str] = []

    class W(_CommandsMixin, _ContextMenuMixin):
        def __init__(self):
            self.client = SimpleNamespace(live_streams=lambda cat: [
                {"name": "Kids", "stream_id": 1, "category_id": "10"},
                {"name": "Late Film", "stream_id": 2, "category_id": "99"}])
            self.pool = None
            self.overrides = SimpleNamespace(excluded_ids=lambda kind, include_locked: {"99"})
            self.channel_ov = SimpleNamespace(anything_hidden=lambda: False)
            self.parental = SimpleNamespace(session_unlocked=False)

        def play_live_channel(self, it):
            played.append(it["name"])

        def _show_toast(self, text, ms=0):
            toasts.append(text)

    from dopeiptv.ui import mw_commands
    w = W()
    orig = mw_commands.run_async
    mw_commands.run_async = lambda pool, fn, done, fail: done(fn())
    try:
        w._play_channel_named("Late Film")
        w._play_channel_named("Kids")
    finally:
        mw_commands.run_async = orig
    assert played == ["Kids"]
    assert toasts == [tr("cmd_no_channel", name="Late Film")]


# -- a multiview cell takes the source's way onto the network ---------------------------

def test_a_multiview_cell_plays_with_the_sources_nerd_options_as_the_player_does(app, monkeypatch):
    from dopeiptv.core import nerd
    from dopeiptv.ui.mw_multiview import _MultiviewCell

    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)

    class Mpv(dict):
        played = None

        def play(self, url):
            self.played = url

    cell = _MultiviewCell.__new__(_MultiviewCell)     # its mpv only, no GL surface
    m = Mpv()
    cell.video = SimpleNamespace(mpv=m, show=lambda: None)
    cell.title, cell._ts_capable, cell._muted = "SVT1", False, True
    cell._want_aid = cell._want_sid = None
    cell._nerd_applied = False
    # Nerd off: nothing of it is set, as before.
    assert cell._mpv_play("http://provider.example/live/1.ts")
    assert "user-agent" not in m and "http-proxy" not in m
    nerd.install(_S(nerd_enabled="true", nerd_dns="cloudflare"))
    nerd.set_source({"kind": "xtream", "options": {"user_agent": "Kodi/21"}})
    try:
        assert cell._mpv_play("http://provider.example/live/1.ts")
        assert m.played == "http://provider.example/live/1.ts"
        assert m["user-agent"] == "Kodi/21"
        # Own DNS (or a trusted certificate): through the route's bridge.
        assert str(m["http-proxy"]).startswith("http://127.0.0.1:")
    finally:
        nerd.set_source(None)
        nerd.install(None)


# -- a trusted certificate and a cast -------------------------------------------------------

def test_a_pin_covers_only_https_to_its_own_server():
    from dopeiptv.core import cert_pin
    opts = cert_pin.pin_options("tv.example.com", 8443, "ab" * 32)
    assert cert_pin.covers(opts, "https://tv.example.com:8443/live/1.m3u8") == ("tv.example.com", 8443)
    assert cert_pin.covers(opts, "http://cdn.example.com/a.ts", "https://TV.example.com:8443/x") == (
        "tv.example.com", 8443)
    assert cert_pin.covers(opts, "https://tv.example.com/live/1.m3u8") is None       # port 443
    assert cert_pin.covers(opts, "http://tv.example.com:8443/live/1.ts") is None     # no TLS
    assert cert_pin.covers({}, "https://tv.example.com:8443/x") is None
    v6 = cert_pin.pin_options("fd00::5", 443, "cd" * 32)
    assert cert_pin.covers(v6, "https://[fd00::5]/live/1.ts") == ("fd00::5", 443)


def test_a_cast_of_a_pinned_source_is_asked_about_once_per_source_this_session(app):
    from dopeiptv.core import cert_pin, nerd
    from dopeiptv.ui.mw_cast import _CastMixin

    class W(_CastMixin):
        def __init__(self):
            self.active = {"id": "a", "name": "Box",
                           "options": cert_pin.pin_options("box.example.com", 443, "ab" * 32)}
            self.playlist_store = SimpleNamespace(active=lambda: self.active)
            self.asked: list[tuple[str, str]] = []
            self.answer = 1

        def _choice_dialog(self, title, message, options, dont_ask_setting=None):
            self.asked.append((title, message))
            return self.answer

    url = "https://box.example.com/live/u/p/1.m3u8"
    w = W()
    assert w._cast_pin_ok(url) and not w.asked                  # Nerd off: no pin in force
    nerd.install(_S(nerd_enabled="true"))
    try:
        assert not w._cast_pin_ok(url)                          # Cancel
        assert w.asked[-1] == (tr("cast_pin_title"),
                               tr("cast_pin_body", name="Box", server="box.example.com:443"))
        assert not w._cast_pin_ok(url) and len(w.asked) == 2    # asked again
        w.answer = 0
        assert w._cast_pin_ok(url) and len(w.asked) == 3        # Cast anyway
        assert w._cast_pin_ok(url) and len(w.asked) == 3        # not again for this source
        assert w._cast_pin_ok("http://box.example.com/live/1.ts", "/home/me/f.mkv")
        w.active = {"id": "b", "name": "Other",
                    "options": cert_pin.pin_options("other.example.com", 443, "cd" * 32)}
        w.answer = 1
        assert not w._cast_pin_ok("https://other.example.com/a.m3u8") and len(w.asked) == 4
        assert w._cast_pin_ok("https://box.example.com/x.m3u8")  # not where this one's pin is
    finally:
        nerd.install(None)
    assert not hasattr(_CastMixin, "_cast_pin_allowed")          # the window's, never stored


# -- the phone's page hears what stopped its play ------------------------------------------

def test_the_phone_note_is_the_phones_only_for_a_while_and_until_something_plays(monkeypatch):
    from dopeiptv.core import phone_page
    from dopeiptv.ui import mw_phone

    clock = [100.0]
    monkeypatch.setattr(mw_phone.time, "monotonic", lambda: clock[0])

    class W(mw_phone._PhoneMixin):
        def __init__(self):
            self.player = SimpleNamespace(current_url="http://h.example.com/live/1.ts")

    w = W()
    w._phone_note("Not asked by the phone")
    assert w._phone_note_now() == ""
    w._phone_asked()
    clock[0] += 2
    w._phone_note(tr("nerd_vpn_blocked"))
    assert w._phone_note_now() == tr("nerd_vpn_blocked")
    clock[0] += mw_phone.PHONE_NOTE_S + 1
    assert w._phone_note_now() == ""                              # its while is over
    w._phone_asked()
    w._phone_note("Over the limit")
    w.player.current_url = "http://h.example.com/live/2.ts"       # it played after all
    assert w._phone_note_now() == ""
    clock[0] += mw_phone.PHONE_ASKED_S + 1
    w._phone_note("Much later")                                   # not the phone's any more
    assert w._phone_note_now() == ""
    page, _policy = phone_page.render({}, {}, {}, "en", False)
    assert "banner(s.note" in page.decode("utf-8")      # shown where it says it is offline


# -- the phone remote and a box's tuners ----------------------------------------------------

_TUNER_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from dopeiptv.core import tuners
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.Format.IniFormat)
w = mw.MainWindow(DemoClient(), settings)
w.show()
app.processEvents()

asked = []
w._show_toast = lambda text, ms=0: None
w._choice_dialog = lambda title, message, options, dont_ask_setting=None: (asked.append(message), len(options) - 1)[1]

# The source is a box with one tuner, its TV on another transponder than
# every channel here; what it said is kept (never asked again in this run).
BOX = "http://box.example.com:8001/"
c = w.client
row = c.live_streams(None)[0]


def never_here():
    raise AssertionError("the box asked on the window's thread")


c.live_url = lambda sid, fmt="ts": BOX + "1:0:19:1:802:1:C00000:0:0:0:"
c.own_stream = lambda url: str(url).startswith(BOX)
c.tuner_state = never_here
c.tuner_key = lambda sid: "802:1:C00000"
w._tuner_states = tuners.StateCache(clock=lambda: 1.0)
w._tuner_states.put(w._conn_source(), tuners.BoxState(1, tuners.Tune("tv", "SVT1 HD", "3FB:1:C00000")))


class D:
    active = True
    cfg = SimpleNamespace(commands=True)
    def emit(self, *a, **k): pass
    def set_state(self, *a, **k): pass
    def set_favorites(self, *a, **k): pass


w._events = D()
w._events_favs = {row["name"]: row}


def playing():
    return bool(w.player is not None and w.player.current_url)


cut = tr("tuner_cuts", what=tr("tuner_w_tv", name="SVT1 HD"))
w._phone_do("_play", {"row": row})
assert asked and cut in asked[-1] and not playing(), ("phone", asked)
note = w._phone_do("state", {}).get("note") or ""
assert cut in note and tr("phone_answer_there") in note, note
asked.clear()
w._events_command("favorite", row["name"])
assert asked and cut in asked[-1] and not playing(), ("mqtt favorite", asked)
asked.clear()
if w._multiview_available():
    w.add_to_multiview(c.live_url(row["stream_id"]), row["name"])
    assert asked and cut in asked[-1] and w._multiview_win is None, ("multiview", asked)
# The list's ring says the same, and a channel beside the TV's is free.
assert w.tuner_mark({"stream_id": row["stream_id"]}, "live")
assert w._tuner_verdict(BOX + "1:0:19:2:3FB:1:C00000:0:0:0:", []) is None
print("TUNERS_WINDOW_OK")
"""


def test_the_phone_remote_and_home_assistant_meet_the_transponder_check():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for _attempt in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-c", _TUNER_CHILD], capture_output=True, text=True,
            env=env, cwd=_REPO_ROOT, timeout=180)
        if "TUNERS_WINDOW_OK" in proc.stdout:
            return
    assert "TUNERS_WINDOW_OK" in proc.stdout, (
        f"a play from the phone or Home Assistant missed the box's tuners\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}")


# -- one count: what plays, and the source in use changed under it ---------------------

def test_what_plays_stays_its_own_sources_after_the_source_in_use_changes():
    """A channel of source A plays on; the person switches to source B (the
    stream goes on - the status line says "from A"). The player's connection
    is still A's account's: counted there, not against B's, and server mode
    joins it for a device that asks for that channel instead of opening a
    second connection to A."""
    from dopeiptv.core import connections
    from dopeiptv.ui.mw_connections import _ConnectionsMixin
    from dopeiptv.ui.mw_server import _WindowHost
    from dopeiptv.ui.mw_source_label import _SourceLabelMixin

    url = "http://a.invalid/live/u/p/7.ts"
    dvr = SimpleNamespace(streaming=True, recording=False, url=url, key="live:k7")

    class Store:
        active_id = "a"

        def get(self, pid):
            return {"id": pid, "name": pid.upper()}

    class W(_ConnectionsMixin, _SourceLabelMixin):
        def __init__(self):
            self.client = SimpleNamespace(max_connections=1)
            self.player = SimpleNamespace(current_url="http://127.0.0.1:1/x", _dvr=dvr,
                                          _dvr_pool=SimpleNamespace(sessions=[dvr]))
            self.rec = SimpleNamespace(jobs=[])
            self._multiview_win = None
            self.playlist_store = Store()
            self._failover_source = None
            self._failover_key = None
            self._last_playback = None
            self.cast = SimpleNamespace(active=None)
            self._playing_item = {"stream_id": 7}
            self.settings = _S(mv_stop_docked="false")
            self.asked: list[str] = []

        def _item_key(self, _it):
            return "k7"

        def _choice_dialog(self, title, message, options, dont_ask_setting=None):
            self.asked.append(message)
            return len(options) - 1

    w = W()
    w._note_playing_source(url, "k7", "live")       # as _start_playback does
    w._last_playback = {"url": url}
    assert [(u.kind, u.source) for u in w._conn_uses()] == [("buffer", "a")]
    host = _WindowHost(w, None)
    assert host.app_session("a", "7") is dvr
    w._conn_ledger().report("a", 1, None, 1)        # A's provider said: one connection

    w.playlist_store.active_id = "b"                # switch_playlist: nothing stops
    uses = w._conn_uses()
    assert [(u.kind, u.source) for u in uses] == [("buffer", "a")]
    assert connections.own_count(uses, "a") == 1 and connections.own_count(uses, "b") == 0
    # A recording or a multiview cell of B's is its first connection to B.
    assert w._conn_verdict(uses) is None
    assert host.app_session("a", "7") is dvr        # joined, not opened once more
    assert host.app_session("b", "7") is None       # B's channel 7 is another channel
    # The docked stream into a multiview cell beside the player (Settings
    # keeps the player): A's second connection, asked about; B's first is not.
    assert not w._conn_ok_multiview(None, url, "a") and len(w.asked) == 1
    assert w._conn_ok_multiview(None, "http://b.invalid/live/u/p/1.ts") and len(w.asked) == 1
    # Play after Stop of the same address is still A's.
    w._note_playing_source(url, "k7", "live")
    assert w._conn_player_source() == "a"
    # A file of one's own, then a channel of B: B's.
    w._note_playing_source("/home/x/film.mkv", "f", "local")
    assert w._conn_player_source() == "b"
    w._note_playing_source("http://b.invalid/live/u/p/7.ts", "k7", "live")
    assert w._conn_player_source() == "b"


# -- one count after 1.6.0: each connection against its own source -----------------------

class _Sources:
    """Two sources, a and b; *active_id* the one in use."""

    def __init__(self, active="a"):
        self.active_id = active

    def get(self, pid):
        return {"id": pid, "name": pid.upper(), "kind": "xtream"} if pid in ("a", "b") else None

    def active(self):
        return self.get(self.active_id)

    def playlists(self):
        return [self.get("a"), self.get("b")]


def test_a_cast_is_counted_against_the_source_it_is_from(monkeypatch):
    """A's channel plays; the person switches to B and casts what plays: the
    TV fetches A's stream - A's own address, not B's channel of the same
    number - and the cast is A's connection: asked about against A's limit,
    counted there (server mode sees it on A), none on B. A row of B's list
    cast is B's."""
    from dopeiptv.ui import mw_cast
    from dopeiptv.ui.mw_cast import _CastMixin
    from dopeiptv.ui.mw_connections import _ConnectionsMixin
    from dopeiptv.ui.mw_source_label import _SourceLabelMixin

    url = "http://a.invalid/live/u/p/7.ts"
    row = {"stream_id": 7, "name": "News", "_kind": "live"}
    opened: list[str] = []

    class Dialog:
        def __init__(self, win, address, title, *a, **k):
            opened.append(address)

        def exec(self):
            return 0

    monkeypatch.setattr(mw_cast, "CastDialog", Dialog)

    class W(_CastMixin, _ConnectionsMixin, _SourceLabelMixin):
        mode, _fav_section, series_ctx = "live", None, None

        def __init__(self):
            self.client = SimpleNamespace(
                max_connections=None,
                live_url=lambda sid, fmt="ts": f"http://b.invalid/live/u/p/{sid}.{fmt}")
            self.player = SimpleNamespace(current_url=url, _dvr=None, _dvr_pool=None)
            # A recording of A's own over a connection of its own.
            self.rec = SimpleNamespace(jobs=[{"status": "recording", "source": "a",
                                              "url": "http://a.invalid/live/u/p/9.ts"}])
            self._multiview_win = None
            self.playlist_store = _Sources("a")
            self._failover_source = self._failover_key = None
            self._last_playback = None
            self.cast = SimpleNamespace(active=None)
            self._cast_ctx = None
            self._cast_device = None
            self._playing_key = self._playing_item = None
            self.settings = _S()
            self.asked: list[str] = []
            self.answer = 1

        def _item_key(self, it):
            return (it or {}).get("stream_id") or (it or {}).get("_key")

        def _content_kind(self):
            return "live"

        def _stream_for(self, it):
            sid = it.get("stream_id")
            return (self.client.live_url(sid) if sid is not None else None), it.get("name")

        def _play_kind_for(self, it):
            return "live" if it.get("_kind") == "live" else "other"

        def _vpn_guard_blocks(self, _url):
            return False

        def _local_codecs(self):
            return []

        def _local_audio_index(self):
            return 0

        def _local_tracks(self, _it):
            return {}

        def _choice_dialog(self, title, message, options, dont_ask_setting=None):
            self.asked.append(message)
            return self.answer

    w = W()
    w._note_playing_source(url, 7, "live")              # as _start_playback does
    w._last_playback = {"url": url, "title": "News", "kind": "live", "item": row}
    w._playing_key, w._playing_item = 7, row
    w._conn_ledger().report("a", 1, None, 2)            # A's provider said: one
    w.playlist_store.active_id = "b"                    # the stream goes on

    w.cast_playing()                                    # A's second connection
    assert len(w.asked) == 1 and tr("conn_what_cast") in w.asked[0], w.asked
    assert tr("conn_over", max=1, n=1) in w.asked[0] and not opened
    w.answer = 0                                        # Go ahead anyway
    w.cast_playing()
    assert opened == [url], "A's own address - not B's channel 7"
    assert w._cast_ctx["source_id"] == "a"
    w.cast.active = object()                            # the TV plays it
    uses = w._conn_uses()
    assert ("cast", "a") in [(u.kind, u.source) for u in uses]
    assert connections.own_count(uses, "b") == 0
    # A row of B's own list: B's.
    w.cast.active, w.player.current_url, w.rec.jobs = None, None, []
    w._open_cast_dialog({"stream_id": 3, "name": "Sport", "_kind": "live"})
    assert opened[-1] == "http://b.invalid/live/u/p/3.m3u8"
    assert w._cast_ctx["source_id"] == "b"
    w.cast.active = object()
    assert [(u.kind, u.source) for u in w._conn_uses()] == [("cast", "b")]


def _source_window():
    """A window with the connections count and the source label: nothing
    plays, no limit known, source a in use."""
    from dopeiptv.ui.mw_connections import _ConnectionsMixin
    from dopeiptv.ui.mw_source_label import _SourceLabelMixin

    class W(_ConnectionsMixin, _SourceLabelMixin):
        def __init__(self):
            self.client = SimpleNamespace(max_connections=None)
            self.player = SimpleNamespace(current_url=None, _dvr=None, _dvr_pool=None)
            self.rec = SimpleNamespace(jobs=[])
            self._multiview_win = None
            self.playlist_store = _Sources("a")
            self._failover_source = self._failover_key = None
            self._last_playback = None
            self.cast = SimpleNamespace(active=None)
            self._cast_ctx = None
            self.settings = _S(mv_stop_docked="true")
            self.asked: list[str] = []
            self.answer = None

        def _item_key(self, it):
            return (it or {}).get("stream_id") or (it or {}).get("_key")

        def _choice_dialog(self, title, message, options, dont_ask_setting=None):
            self.asked.append(message)
            return len(options) - 1 if self.answer is None else self.answer
    return W


def test_a_stream_the_player_gave_up_on_holds_no_connection():
    """A live channel dropped and nothing opens it again (Settings: no
    reconnect): its last picture stays until Stop, but it holds no
    connection - not counted, so a test, a recording or server mode's
    device is not refused for it. A stall may still be read, and the slow
    reconnect wants it back: both still counted. The next play of the very
    channel is counted again. From the rewind buffer: not counted once the
    buffer's own connection gave up too."""
    from dopeiptv.ui.main_window import MainWindow

    url = "http://a.invalid/live/u/p/7.ts"

    class P:
        def __init__(self, dvr=None):
            self.current_url, self._dvr, self._dvr_pool = url, dvr, None
            self._play_gen, self.visible = 3, True

        def isVisible(self):  # noqa: N802 - Qt's name
            return self.visible

        def set_overlay_info(self, _text):
            pass

        def hide_loading(self):
            pass

        def playback_position(self):
            return 0.0

    class W(_source_window()):
        MAX_STREAM_RETRIES = MainWindow.MAX_STREAM_RETRIES
        _reconnect_live = MainWindow._reconnect_live
        _auto_reconnect_live = MainWindow._auto_reconnect_live

        def _health_drop(self):
            pass

        def _health_stuck(self):
            pass

        def _set_status(self, *_a, **_k):
            pass

        def _arm_slow_reconnect(self):
            self.slow = True

    w = W()
    w.player = P()
    w._note_playing_source(url, 7, "live")
    w._last_playback = {"url": url, "kind": "live", "key": 7}
    w.settings["auto_reconnect_live"] = "false"

    def counted():
        return [(u.kind, u.source) for u in w._conn_uses()]

    w._reconnect_live("stall")              # mpv may still be reading it
    assert counted() == [("player", "a")]
    w._reconnect_live("eof")                # ended, and nothing opens it again
    assert counted() == []
    w.client.max_connections = 1
    assert w._conn_ok_test("a") and not w.asked, "a test of A reads without a question"
    w.player._play_gen += 1                 # the same channel played again
    assert counted() == [("player", "a")]
    # Reconnects on: the quick ones spent, the slow one wants it back.
    w.settings["auto_reconnect_live"] = "true"
    w._stream_retries, w._last_stream_error_ts = MainWindow.MAX_STREAM_RETRIES, 9e12
    w._reconnect_live("eof")
    assert getattr(w, "slow", False) and counted() == [("player", "a")]
    # No player on screen to open it again: given up too.
    w.player.visible = False
    w._reconnect_live("eof")
    assert counted() == []
    # From the rewind buffer: its connection gave up too - nothing; one
    # still streaming (a recording in the player reads it) is counted.
    w.settings["auto_reconnect_live"] = "false"
    dvr = SimpleNamespace(streaming=True, recording=False, url=url, state="failed")
    w.player = P(dvr)
    w._reconnect_live("eof")
    assert counted() == []
    dvr.state = "streaming"
    assert counted() == [("buffer", "a")]


class _Ingest:
    """A provider connection that only says whether it is open."""

    def __init__(self, url, store):
        self.url, self.store, self.state, self.connections = url, store, "streaming", 1

    def start(self):
        pass

    def stop(self, wait=0.0):
        self.state = "stopped"


def test_a_buffer_a_recording_or_a_cast_keeps_is_its_own_source_s(tmp_path):
    """A's channel watched from the rewind buffer, kept streaming by a cast
    (or a recording) after the player moved on: that buffer is A's
    connection - counted on A, none on B, so server mode serves B's devices.
    Server mode's own session is its source's from the start."""
    from dopeiptv.core.dvr_pool import DvrPool
    from dopeiptv.media.dvr_player import DvrPlayerMixin

    pool = DvrPool(tmp_path, 600.0, 10**9, ingest_factory=_Ingest, min_free=0)

    class P(DvrPlayerMixin):
        def __init__(self):
            self._settings = _S(live_rewind_kept="true")
            self._dvr_pool, self._dvr, self.current_url = pool, None, None

        def _live_rewind_minutes(self):
            return 10

    try:
        w = _source_window()()
        p = P()
        p.source_of = w._conn_player_source         # as the window sets it
        a_url = "http://a.invalid/live/u/p/7.ts"
        w._note_playing_source(a_url, 7, "live")
        w._last_playback = {"url": a_url}
        assert p._dvr_start(a_url, "live:7")
        kept = p._dvr
        kept.hold("cast-live:1")                    # the TV may go back in it
        p._end_dvr()                                # the player moves on
        assert kept.streaming
        w.player = SimpleNamespace(current_url=None, _dvr=None, _dvr_pool=pool)
        w.playlist_store.active_id = "b"
        uses = w._conn_uses()
        assert [(u.kind, u.source) for u in uses] == [("buffer", "a")]
        assert connections.own_count(uses, "b") == 0
        srv = pool.acquire("http://b.invalid/live/u/p/1.ts", "srv:b\x1f1", "server",
                           source="b")
        assert srv.source == "b"
    finally:
        pool.close_all()


def test_a_web_stream_or_a_station_is_no_provider_s_connection():
    """YouTube or Twitch through yt-dlp, or an internet radio station, is no
    source's: not counted against the source in use - a test of its stream
    or server mode's device is not refused for it. A channel is counted."""
    w = _source_window()()
    w.client.max_connections = 1
    for kind, url in (("web", "ytdl://https://www.youtube.com/watch?v=x"),
                      ("web", "https://rr1.googlevideo.example/videoplayback?id=1"),
                      ("radio", "http://ice.example.org/stream.mp3")):
        w.player.current_url = url
        w._note_playing_source(url, url, kind)
        w._last_playback = {"url": url, "kind": kind}
        assert w._conn_uses() == [], kind
        assert w._conn_ok_test("a") and not w.asked, kind
    url = "http://a.invalid/live/u/p/7.ts"
    w.player.current_url = url
    w._note_playing_source(url, 7, "live")
    w._last_playback = {"url": url, "kind": "live"}
    assert [(u.kind, u.source) for u in w._conn_uses()] == [("player", "a")]
