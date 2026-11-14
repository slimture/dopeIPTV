"""Stream health (Nerd): per channel the starts, what did not play, the
time to the picture (last and on average), drops, the last error scrubbed
of addresses and logins, when it last played - in one settings value of at
most 500 channels. Nothing while Nerd or its switch is off, nothing during
private viewing; in the window, driven through the playback path's own
hooks, and it never changes how anything plays."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from dopeiptv.core import health, nerd, privacy
from dopeiptv.core.health import STORE_KEY, StreamHealth

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class S(dict):
    """The app's settings, as far as stream health uses them."""

    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def remove(self, key):
        self.pop(key, None)


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _clean():
    yield
    nerd.install(None)
    privacy.set_private(False)


def _store(s=None, **kw):
    kw.setdefault("on", lambda: True)
    kw.setdefault("mono", Clock(100.0))
    kw.setdefault("clock", Clock(1_700_000_000.0))
    return StreamHealth(S() if s is None else s, **kw)


def test_nothing_until_nerd_and_its_switch_are_on():
    s = S()
    h = StreamHealth(s)
    nerd.install(s)
    assert not health.enabled()
    assert not h.begin("a:1", "TV4") and h.frame() is None and not h.fail("x")
    s["nerd_enabled"] = "true"
    nerd.install(s)
    assert not health.enabled() and not h.begin("a:1", "TV4")
    assert STORE_KEY not in s                      # nothing changes until you change something
    s["nerd_health"] = "true"
    nerd.install(s)
    assert health.enabled() and h.begin("a:1", "TV4")
    assert json.loads(s[STORE_KEY])["a:1"]["starts"] == 1
    # The switch on but Nerd off: nothing either.
    s["nerd_enabled"] = "false"
    nerd.install(s)
    assert not h.begin("a:1", "TV4")
    assert h.entry("a:1")["starts"] == 1


def test_private_viewing_keeps_nothing():
    h = _store()
    privacy.set_private(True)
    assert not h.begin("a:1", "TV4") and h.entries() == {}
    privacy.set_private(False)
    assert h.begin("a:1", "TV4")


def test_a_start_its_picture_and_the_average():
    mono, wall = Clock(100.0), Clock(1_700_000_000.0)
    s = S()
    h = _store(s, mono=mono, clock=wall)
    assert h.begin("a:7", "TV4 HD") and h.current == "a:7"
    mono.t = 102.5
    # Half a second had already played when the player noticed.
    assert h.frame(0.25) == 2.25
    assert h.frame() is None                       # once per start
    assert h.begin("a:7", "TV4 HD")
    mono.t += 4.0
    assert h.frame() == 4.0
    e = h.entry("a:7")
    assert (e["starts"], e["ttff"], e["ttff_n"]) == (2, 4.0, 2)
    assert health.average(e) == pytest.approx(3.125) and health.last_start(e) == 4.0
    assert e["at"] == 1_700_000_000 and e["name"] == "TV4 HD"
    assert health.grade(e) == "ok"
    # The next run reads the same back from the settings.
    assert _store(s).entry("a:7") == e


def test_a_reconnect_is_no_new_start_and_a_drop_counts_once():
    mono = Clock(100.0)
    h = _store(mono=mono)
    h.begin("a:7", "TV4 HD")
    mono.t += 1
    h.frame()
    assert h.drop()                                # it played, then dropped
    assert not h.drop()                            # the same drop, seen again
    h.expect_retry()
    assert not h.begin("a:7", "TV4 HD")            # the app reconnecting it
    mono.t += 5
    assert h.frame() is None                       # no second time to picture
    assert h.drop()
    e = h.entry("a:7")
    assert (e["starts"], e["drops"], e["ttff_n"], e["ttff"]) == (1, 2, 1, 1.0)
    # A retry asked for never makes another channel's start a reconnect.
    h.expect_retry()
    assert h.begin("a:8", "SVT1") and h.entry("a:8")["starts"] == 1
    # Nor does it outlive a film started meanwhile.
    h.expect_retry()
    h.leave()
    assert h.begin("a:8", "SVT1") and h.entry("a:8")["starts"] == 2


def test_a_failure_counts_once_and_keeps_the_reason():
    h = _store()
    h.begin("a:9", "Kanal 9")
    assert h.fail("loading failed")
    assert not h.fail("Account expired")           # the diagnosis, for the same try
    e = h.entry("a:9")
    assert (e["fails"], e["err"], e["bad"]) == (1, "Account expired", True)
    assert health.grade(e) == "error"
    # A diagnosis that comes back after the next channel started: only its reason.
    h.begin("a:10", "Kanal 10")
    assert not h.fail("Forbidden (403)", key="a:9")
    assert (h.entry("a:9")["fails"], h.entry("a:9")["err"]) == (1, "Forbidden (403)")
    assert "fails" not in h.entry("a:10")
    # Nothing tried, nothing known: nothing to say.
    h.leave()
    assert not h.fail("loading failed") and not h.fail("x", key="a:404")
    # Played again: no longer red while half of the tries failed, then amber.
    for _ in range(2):
        h.begin("a:9", "Kanal 9")
        h.frame()
    e = h.entry("a:9")
    assert not e["bad"] and health.grade(e) == "warn"


def test_a_start_that_never_showed_a_picture_is_a_failure():
    h = _store()
    h.begin("a:3", "Frozen")
    assert h.stuck("The stream sent no picture.")
    assert not h.stuck("again")
    assert h.entry("a:3")["fails"] == 1
    h.begin("a:4", "Plays")
    h.frame()
    assert not h.stuck("x")                       # it played: drops are drop()'s
    assert "fails" not in h.entry("a:4")


def test_the_error_keeps_no_address_or_login():
    s = S()
    h = _store(s)
    h.begin("a:1", "TV4")
    h.fail("Failed to open http://anna:pw-anna@203.0.113.7:8080/live/anna/pw-anna/1.ts\n"
           "(token=abc123) via 10.1.2.3 " + "x" * 400)
    err = h.entry("a:1")["err"]
    for secret in ("pw-anna", "anna", "203.0.113.7", "abc123", "10.1.2.3"):
        assert secret not in err and secret not in s[STORE_KEY]
    assert err.startswith("Failed to open http://<address> (token=<hidden>")
    assert "\n" not in err and len(err) <= health.MAX_ERROR


def test_at_most_500_channels_the_oldest_go_first():
    wall = Clock(1000.0)
    s = S()
    h = _store(s, clock=wall)
    for i in range(505):
        wall.t = 1000.0 + i
        h.begin(f"a:{i}", f"Channel {i}")
    kept = json.loads(s[STORE_KEY])
    assert len(h.entries()) == len(kept) == health.CAP == 500
    assert not any(f"a:{i}" in kept for i in range(5)) and "a:5" in kept and "a:504" in kept
    # Played again, a channel is kept over one played after it.
    wall.t += 1
    h.begin("a:5", "Channel 5")
    wall.t += 1
    h.begin("a:new", "New")
    assert "a:5" in h.entries() and "a:6" not in h.entries()
    assert list(health.capped({"x": {"at": 1}, "y": {"at": 3}, "z": {"at": 2}}, 2)) == ["y", "z"]


def test_clear_forgets_every_channel():
    s = S()
    h = _store(s)
    h.begin("a:1", "TV4")
    h.fail("x")
    h.clear()
    assert h.entries() == {} and STORE_KEY not in s and h.current is None
    assert h.begin("a:1", "TV4") and h.entry("a:1")["starts"] == 1


def test_the_worst_channels_first():
    def ch(starts, fails=0, ttff=None, drops=0):
        e = {"starts": starts, "fails": fails, "drops": drops}
        if ttff is not None:
            e.update(ttff=ttff, ttff_sum=ttff * (starts - fails), ttff_n=starts - fails)
        return e
    s = S({STORE_KEY: json.dumps({"fine": ch(4, ttff=1.0), "slow": ch(4, ttff=8.0),
                                  "flaky": ch(10, fails=1, ttff=1.0), "dead": ch(4, fails=3, ttff=2.0),
                                  "dropping": ch(2, ttff=1.0, drops=3)})})
    h = _store(s)
    assert [k for k, _e in h.worst()] == ["dead", "flaky", "slow", "dropping", "fine"]
    assert [k for k, _e in h.worst(2)] == ["dead", "flaky"]
    grades = {k: health.grade(e) for k, e in h.entries().items()}
    assert grades == {"fine": "ok", "slow": "warn", "flaky": "warn", "dead": "error", "dropping": "warn"}
    assert health.grade({}) == health.grade({"starts": 1}) == ""   # no picture, no failure yet


def test_what_is_stored_is_read_with_care():
    for raw in ("not json", "[1, 2]", "", None, 5):
        assert _store(S({STORE_KEY: raw})).entries() == {}
    h = _store(S({STORE_KEY: json.dumps({"a:1": 5, "a:2": {"starts": "x", "fails": 2}})}))
    assert list(h.entries()) == ["a:2"]
    assert health.counts(h.entry("a:2")) == (0, 2, 0) and health.grade(h.entry("a:2")) == "error"


def test_a_channel_is_its_source_and_its_stream():
    assert health.channel_key("a", 7) == "a:7"
    assert health.channel_key(None, "1:0:19:2B66:3F3:1:C00000:0:0:0:") == ":1:0:19:2B66:3F3:1:C00000:0:0:0:"


# -- Settings: the card on Nerd's "Channels and guide" page -----------------------

@pytest.fixture(scope="module")
def app():
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    return widgets.QApplication.instance() or widgets.QApplication([])


def test_the_settings_card_lists_the_worst_stores_the_switch_and_clears(app, tmp_path):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QDialog, QPushButton

    from dopeiptv.i18n import tr
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.settings_ui import SettingsPage, ToggleSwitch
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    nerd.install(s)
    store = _store(s)
    store.begin("a:1", "TV4 HD")
    store.fail("loading failed")
    store.begin("a:2", "SVT1")
    store.frame()
    page = SettingsPage("nerd", "Nerd", "")
    commit = nerd_settings.build(page, s, QDialog(), health_store=lambda: store)
    shown = page.findChildren(nerd_settings.HealthList)[0]
    assert [name for name, _n in shown.rows] == ["TV4 HD", "SVT1"]
    assert shown.rows[0][1] == tr("nerd_health_line", fails=1, starts=1, avg="–", drops=0)
    assert "nerd_health" in nerd_settings.PART_KEYS["lists"]
    switch = next(sw for sw in page.findChildren(ToggleSwitch) if sw.accessibleName() == tr("nerd_health"))
    assert not switch.isChecked()                 # off by default
    stored: dict = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert "nerd_health" not in stored            # untouched: not written
    switch.setChecked(True)
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored["nerd_health"] == "true"
    clear = next(b for b in page.findChildren(QPushButton) if b.text() == tr("nerd_health_clear"))
    assert clear.isEnabled()
    clear.click()
    assert shown.rows == [] and store.entries() == {} and not clear.isEnabled()
    assert s.value(STORE_KEY) is None


# -- in the window: the playback path's own hooks -----------------------------------

_CHILD = r"""
import json, os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication, QMenu
app = QApplication.instance() or QApplication([])

from dopeiptv.core import nerd
from dopeiptv.core.health import STORE_KEY
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui.theme import P


class Client(DemoClient):
    def live_url(self, stream_id, fmt="ts"):
        return f"http://127.0.0.1:9/{stream_id}.ts"


def external(*_a, **_k):
    raise AssertionError("an external player was started")


def until(done, secs=10.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not done():
        app.processEvents()
        w.pool.waitForDone(10)
        time.sleep(0.01)
    return done()


mw.launch_player = external
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "a", "name": "Main", "kind": "m3u",
                                     "server": "http://127.0.0.1:9/a.m3u"}]))
s.setValue("active_playlist", "a")
s.setValue("nerd_enabled", "true")
s.setValue("nerd_health", "true")
nerd.install(s)
w = mw.MainWindow(Client(), s, PlaylistStore(s))
if w.player is None:
    print("HEALTH_SKIP: no embedded player here")
    sys.exit(0)
# The player itself is not under test: what it is asked, and that it shows.
played = []
w.player.play = lambda url, *a, **k: played.append(url) or True
w.player.isVisible = lambda: True
w.wake.acquire = w.wake.release = lambda *a, **k: None
clock = [100.0]
w.health._mono = lambda: clock[0]

# A start, then the picture - the player's own signal, as its poll raises it.
tv4 = {"name": "TV4 HD", "stream_id": 7}
w._start_playback("http://127.0.0.1:9/7.ts", "TV4 HD", None, 7, "live", item=tv4)
assert played == ["http://127.0.0.1:9/7.ts"] and w.health.current == "a:7", (played, w.health.current)
clock[0] = 102.0
w.player.first_frame.emit(0.5)
e = w.health.entry("a:7")
assert (e["starts"], e["ttff"], e["name"]) == (1, 1.5, "TV4 HD"), e
assert w.health_dot(tv4, "live") == P["ok"]

# The provider ends the stream: a drop, and the app's reconnect is no new start.
w._on_player_finished()
assert until(lambda: len(played) == 2), played
w.player.first_frame.emit(0.0)
e = w.health.entry("a:7")
assert (e["starts"], e["drops"], e["ttff_n"]) == (1, 1, 1), e

# A channel that will not play: the reconnects give up once, then the diagnosis.
k9 = {"name": "Kanal 9", "stream_id": 9}
w._start_playback("http://127.0.0.1:9/9.ts", "Kanal 9", None, 9, "live", item=k9)
msg = "loading failed for http://anna:pw-anna@203.0.113.7/live/anna/pw-anna/9.ts"
for _ in range(3):
    w._playback_error(msg)
e = dict(w.health.entry("a:9"))
assert (e["starts"], e["fails"], e["bad"]) == (1, 1, True), e
assert e["err"].startswith("loading failed for http://<address>"), e
assert "pw-anna" not in e["err"] and "203.0.113.7" not in e["err"], e
# The diagnosis says why, for the same channel - and it is no second failure.
assert until(lambda: w.health.entry("a:9")["err"] != e["err"]), w.health.entry("a:9")
assert w.health.entry("a:9")["fails"] == 1
assert w.health_dot(k9, "live") == P["error"]

# The channel's menu, and its numbers.
menu = QMenu()
w._health_channel_menu(menu, k9)
assert [a.text() for a in menu.actions()] == [tr("ctx_stream_health")]
d = w._stream_health_dialog(k9)
assert d.summary.text() == tr("health_sum_error")
assert dict(d.lines)[tr("health_fails")] == "1" and dict(d.lines)[tr("health_starts")] == "1"
assert tr("health_error") in dict(d.lines)

# An address played as it is is no channel: no address is kept as one.
w._play_address("http://anna:pw-anna@127.0.0.1:9/raw.ts")
assert w.health.current is None
stored = s.value(STORE_KEY)
assert set(json.loads(stored)) == {"a:7", "a:9"} and "pw-anna" not in stored, stored

# Off - the switch, or Nerd itself: nothing recorded, no entry, no dot.
for nerd_on, switch in (("true", "false"), ("false", "true")):
    s.setValue("nerd_enabled", nerd_on)
    s.setValue("nerd_health", switch)
    nerd.install(s)
    w._start_playback("http://127.0.0.1:9/11.ts", "Kanal 11", None, 11, "live",
                      item={"name": "Kanal 11", "stream_id": 11})
    w.player.first_frame.emit(0.0)
    assert w.health.entry("a:11") is None
    assert w.health_dot(k9, "live") is None
    menu = QMenu()
    w._health_channel_menu(menu, k9)
    assert menu.actions() == []
assert s.value(STORE_KEY) == stored
print("HEALTH_OK", flush=True)
# Out before Qt's teardown, which the GL widget can crash offscreen.
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    # Nothing of the user's: the app's own folders go to the test's.
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_window_records_through_the_playback_hooks(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=_child_env(tmp_path),
                         capture_output=True, text=True, timeout=180)
    if "HEALTH_SKIP" in out.stdout:
        pytest.skip(out.stdout.strip())
    assert "HEALTH_OK" in out.stdout, out.stdout + out.stderr[-3000:]


# -- in the player: its position poll says when the picture is on -----------------

_PLAYER_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
p = EmbeddedPlayer(settings=settings)


class FakeMpv(dict):
    pause = False
    core_idle = True
    playback_time = None

    def command(self, *args):
        pass

    def play(self, url):
        pass

    def __getattr__(self, n):
        return None


m = p.video.mpv = FakeMpv()
told, stalled = [], []
p.first_frame.connect(told.append)
p.stalled.connect(lambda: stalled.append(1))
assert p.play("http://x/live/1.ts", "Channel", local_buffer=True)
p._poll_position()                       # still opening: nothing yet
m.playback_time, m.core_idle = 0.0, True
p._poll_position()                       # loaded, not playing yet
assert told == []
m.playback_time, m.core_idle = 0.4, False
p._poll_position()
p._poll_position()
assert told == [0.4], told               # once per play()
# The next stream: told again; a time far from 0 is not taken off.
assert p.play("http://x/live/2.ts", "Channel 2", local_buffer=True)
m.playback_time = 37.0
p._poll_position()
assert told == [0.4, 0.0], told
# Paused: not playing, so not told until it plays.
assert p.play("http://x/live/3.ts", "Channel 3", local_buffer=True)
m.playback_time, m.pause = 0.2, True
p._poll_position()
assert told == [0.4, 0.0], told
m.pause = False
p._poll_position()
assert told == [0.4, 0.0, 0.2] and stalled == [], (told, stalled)
# Nothing playing (stopped): never told.
assert p.play("http://x/live/4.ts", "Channel 4", local_buffer=True)
p.current_url = None
p._poll_position()
assert told == [0.4, 0.0, 0.2], told
print("FIRST_FRAME_OK", flush=True)
os._exit(0)
"""


def test_the_player_tells_once_per_stream_when_its_picture_is_on(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    out = subprocess.run([sys.executable, "-c", _PLAYER_CHILD], cwd=_REPO_ROOT, env=_child_env(tmp_path),
                         capture_output=True, text=True, timeout=180)
    assert "FIRST_FRAME_OK" in out.stdout, out.stdout + out.stderr[-3000:]
