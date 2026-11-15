"""The features against each other (docs/MAINTAINING.md, "Every feature works
with the others"): casting next to the rewind buffer in one count of the
account's connections, Watch together through a failover, private viewing
and every reporter - during it and for the rest of a playback that was
private - the statistics and Discord on a channel switch, and the phone
remote, Home Assistant, casting and multiview meeting the VPN guard and the
connection limit the way the player does."""
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
