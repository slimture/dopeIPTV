"""Settings of a channel's own (Nerd): a channel - its source's id and its
stream's id - keeps its own audio and subtitle language, deinterlacing,
aspect ratio and audio delay in one settings value; the audio delay kept
the old way (``nerd_adelay/<crc>``) moves in when its channel is read. In
the player they win over the app's settings while that channel plays, its
catch-up too, and the next stream gets the app's own back; with Nerd off
nothing of them is applied, and they are kept."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zlib

import pytest

from dopeiptv.core import channel_prefs, health, nerd
from dopeiptv.core.channel_prefs import STORE_KEY, ChannelPrefs

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class S(dict):
    """The app's settings, as far as the store uses them."""

    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def remove(self, key):
        self.pop(key, None)


@pytest.fixture(autouse=True)
def _clean():
    yield
    nerd.install(None)


# -- the store ---------------------------------------------------------------------

def test_a_channel_is_its_source_and_its_stream_as_stream_health_keys_it():
    assert channel_prefs.channel_key("a", 7) == "a:7" == health.channel_key("a", 7)
    assert channel_prefs.channel_key(None, "7") == ":7"


def test_what_is_kept_and_default_is_not():
    s = S()
    store = ChannelPrefs(s)
    kept = store.put("a:7", {"alang": "GER", "slang": "off", "deint": "on", "aspect": "4:3",
                             "adelay": 0.25})
    assert kept == {"alang": "ger", "slang": "off", "deint": "on", "aspect": "4:3", "adelay": 0.25}
    assert json.loads(s[STORE_KEY]) == {"a:7": kept}
    # "Default" (empty) and anything a channel may not keep are left out.
    assert store.put("a:8", {"alang": "", "slang": "x y", "deint": "maybe", "aspect": "9:1",
                             "adelay": "nan", "volume": 100}) == {}
    assert store.get("a:8") == {} and not store.has_own("a:8")
    assert set(json.loads(s[STORE_KEY])) == {"a:7"}
    # A second store over the same settings reads what the first wrote.
    assert ChannelPrefs(s).get("a:7") == kept
    assert ChannelPrefs(s).get("b:7") == {}        # another source's channel 7


def test_reset_puts_a_channel_back_to_the_apps_settings():
    s = S()
    store = ChannelPrefs(s)
    store.put("a:7", {"deint": "off"})
    store.put("a:9", {"aspect": "stretch"})
    s[channel_prefs.legacy_key("live:7")] = "0.5"
    store.reset("a:7", channel_prefs.legacy_key("live:7"))
    assert store.get("a:7") == {} and channel_prefs.legacy_key("live:7") not in s
    assert store.get("a:9") == {"aspect": "stretch"}
    store.reset("a:9")
    assert STORE_KEY not in s                       # nothing left: the value goes


def test_the_old_audio_delay_moves_in_once():
    s = S()
    old = channel_prefs.legacy_key("live:7")
    assert old == f"nerd_adelay/{zlib.crc32(b'live:7'):08x}"     # the player's old key
    s[old] = "0.75"
    store = ChannelPrefs(s)
    assert store.get("a:7", old) == {"adelay": 0.75}
    assert old not in s and json.loads(s[STORE_KEY]) == {"a:7": {"adelay": 0.75}}
    assert store.get("a:7", old) == {"adelay": 0.75}             # read again: as kept
    # A channel that already has its own delay keeps it; the old key just goes.
    s[old] = "-1"
    store.set_delay("a:7", 0.1)
    assert store.get("a:7", old) == {"adelay": 0.1} and old not in s
    # A delay of 0 kept the old way is no setting of its own.
    s[channel_prefs.legacy_key("live:8")] = "0"
    assert store.get("a:8", channel_prefs.legacy_key("live:8")) == {}
    assert set(json.loads(s[STORE_KEY])) == {"a:7"}


def test_the_players_delay_is_kept_and_zero_removes_it():
    s = S()
    store = ChannelPrefs(s)
    assert store.set_delay("a:7", 0.5) == {"adelay": 0.5}
    store.put("a:7", dict(store.get("a:7"), deint="on"))
    assert store.set_delay("a:7", 0) == {"deint": "on"}
    assert store.set_delay("a:7", 99) == {"deint": "on", "adelay": channel_prefs.MAX_DELAY}


def test_what_is_stored_is_read_with_care():
    for raw in (None, "", "{", "[]", "3", json.dumps({"a:7": "x", "a:8": {"deint": 5}})):
        assert ChannelPrefs(S({STORE_KEY: raw})).entries() == {}
    raw = json.dumps({"a:7": {"deint": "on", "junk": 1}})
    assert ChannelPrefs(S({STORE_KEY: raw})).get("a:7") == {"deint": "on"}


def test_at_most_cap_channels_the_longest_unchanged_go_first():
    s = S()
    store = ChannelPrefs(s, cap=3)
    for sid in range(1, 5):
        store.put(f"a:{sid}", {"deint": "on"})
    assert list(store.entries()) == ["a:2", "a:3", "a:4"]
    store.put("a:2", {"deint": "off"})          # changed: newest now
    store.put("a:5", {"deint": "on"})
    assert list(store.entries()) == ["a:4", "a:2", "a:5"]


def test_on_only_while_nerd_is():
    assert not channel_prefs.enabled(nerd.Nerd(S()))
    assert channel_prefs.enabled(nerd.Nerd(S(nerd_enabled="true")))
    assert channel_prefs.delay_enabled(nerd.Nerd(S(nerd_enabled="true")))       # on by default
    assert not channel_prefs.delay_enabled(nerd.Nerd(S(nerd_enabled="true",
                                                        nerd_adelay_per_channel="false")))
    assert not channel_prefs.delay_enabled(nerd.Nerd(S(nerd_adelay_per_channel="true")))


# -- what it asks of mpv -------------------------------------------------------------

def test_a_language_and_the_ways_a_stream_tags_it():
    assert channel_prefs.spellings("ger") == ["ger", "de", "deu"]
    assert channel_prefs.spellings("deu") == ["deu", "ger", "de"]
    assert channel_prefs.spellings("nl") == ["nl", "dut", "nld"]
    assert channel_prefs.spellings("xyz") == ["xyz"]
    assert channel_prefs.spellings("not a code") == []


def test_audio_language_first_the_apps_after_it():
    assert channel_prefs.audio_langs({}, "swe") == "swe"          # none: the app's, as it was
    assert channel_prefs.audio_langs({}, "") == ""
    assert channel_prefs.audio_langs({"alang": "ger"}, "swe") == "ger,de,deu,swe"
    assert channel_prefs.audio_langs({"alang": "eng"}, "") == "eng,en"


def test_subtitles_on_a_live_channel_and_its_catch_up():
    assert channel_prefs.subtitle_props({}, True) == []
    assert channel_prefs.subtitle_props({"slang": "off"}, True) == [("sid", "no")]
    assert channel_prefs.subtitle_props({"slang": "off"}, False) == [("sub-visibility", False)]
    assert channel_prefs.subtitle_props({"slang": "eng"}, True, ["swe"]) == [
        ("subs-fallback", "default"), ("sid", "auto"), ("slang", "eng,en,swe"),
        ("sub-visibility", True)]
    # The catch-up opens like a film: a track chosen hidden, shown by the rule.
    assert channel_prefs.subtitle_props({"slang": "eng"}, False, ["eng", "sv"]) == [
        ("sid", "auto"), ("slang", "eng,en,sv"), ("subs-fallback", "yes"),
        ("sub-visibility", False)]
    assert channel_prefs.subtitle_rule({"slang": "eng"}, False) == ("lang", ["eng", "en"])
    assert channel_prefs.subtitle_rule({"slang": "eng"}, True) is None
    assert channel_prefs.subtitle_rule({"slang": "off"}, False) is None
    assert not channel_prefs.own_subtitles({}) and channel_prefs.own_subtitles({"slang": "off"})


def test_picture_deinterlace_aspect_and_the_delay():
    assert channel_prefs.deinterlace({}, True) is True
    assert channel_prefs.deinterlace({"deint": "off"}, True) is False
    assert channel_prefs.deinterlace({"deint": "on"}, False) is True
    assert channel_prefs.aspect_mode({}, "16:9") == "16:9"
    assert channel_prefs.aspect_mode({"aspect": "auto"}, "16:9") == "auto"
    assert channel_prefs.aspect_props("auto") == [("keepaspect", True), ("video-aspect-override", "-1")]
    assert channel_prefs.aspect_props("21:9") == [("keepaspect", True), ("video-aspect-override", "21:9")]
    assert channel_prefs.aspect_props("stretch") == [("keepaspect", False)]
    assert channel_prefs.delay("0.25") == 0.25 and channel_prefs.delay(-30) == -channel_prefs.MAX_DELAY
    for junk in (None, "x", True, float("inf"), 0.0004):
        assert channel_prefs.delay(junk) == 0.0


def _libmpv():
    try:
        import mpv
        return mpv.MPV(vo="null", ao="null", terminal=False)
    except Exception as e:     # no libmpv here: CI installs it, a dev box may not
        pytest.skip(f"libmpv not available: {e}")


def test_a_real_mpv_takes_every_value_the_settings_ask_for():
    m = _libmpv()
    try:
        props = [("alang", channel_prefs.audio_langs({"alang": code}, "swe"))
                 for code in channel_prefs.LANGUAGES]
        for slang in ("off", "eng", "chi"):
            for live in (True, False):
                props += channel_prefs.subtitle_props({"slang": slang}, live, ["swe"])
        for mode in channel_prefs.ASPECTS:
            props += channel_prefs.aspect_props(mode)
        props += [("deinterlace", channel_prefs.deinterlace({"deint": d}, False))
                  for d in channel_prefs.DEINT]
        props.append(("audio-delay", channel_prefs.delay(-2.5)))
        for key, value in props:
            m[key] = value                   # raises if this libmpv refuses it
    finally:
        m.terminate()


# -- the dialog ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    return widgets.QApplication.instance() or widgets.QApplication([])


def test_the_dialog_shows_saves_and_resets(app):
    from dopeiptv.i18n import tr
    from dopeiptv.ui.dialogs import ChannelSettingsDialog
    d = ChannelSettingsDialog(None, "TV4 HD", {"alang": "ger", "adelay": 0.25})
    assert d.marker.text().endswith(tr("chanprefs_own")) and d.reset_btn.isEnabled()
    assert d.alang.currentData() == "ger" and d.delay.value() == 250
    assert d.slang.itemData(1) == "off" and d.slang.currentData() == ""
    d.deint.setCurrentIndex(d.deint.findData("on"))
    d.aspect.setCurrentIndex(d.aspect.findData("stretch"))
    assert d.choice() == {"alang": "ger", "deint": "on", "aspect": "stretch", "adelay": 0.25}
    d.reset()
    assert d.choice() == {} and d.marker.text() == tr("chanprefs_none")
    assert not d.reset_btn.isEnabled()
    # Without the delay per channel, its row is not offered and its value stays.
    d = ChannelSettingsDialog(None, "TV4 HD", {"adelay": -0.5, "slang": "zza"}, delay=False)
    assert d.delay is None and d.choice() == {"adelay": -0.5, "slang": "zza"}
    assert d.slang.currentText() == "zza"           # a code no longer offered is kept


# -- in the player: a real EmbeddedPlayer over a stand-in mpv ---------------------------

_PLAYER_CHILD = r"""
import json, os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
from dopeiptv.core import channel_prefs, nerd
from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
for k, v in {"audio_lang": "swe", "sub_mode": "auto", "video_deinterlace": "false",
             "aspect_mode": "auto", "nerd_enabled": "true"}.items():
    s.setValue(k, v)
nerd.install(s)
p = EmbeddedPlayer(settings=s)


class FakeMpv(dict):
    pause = False

    def __init__(self):
        super().__init__()
        self.sets = []

    def __setitem__(self, k, v):
        self.sets.append((k, v))
        super().__setitem__(k, v)

    def command(self, *args):
        pass

    def play(self, url):
        pass

    def __getattr__(self, n):
        return None


m = p.video.mpv = FakeMpv()
GLOBAL = {"alang": "swe", "deinterlace": False, "keepaspect": True,
          "video-aspect-override": "-1", "sid": "auto", "audio-delay": 0.0}


def now(*keys):
    return {k: m.get(k) for k in keys}


def play(url, chan=None, live=True, key=None):
    if chan is not None:
        p.set_channel(chan)
    m.sets.clear()
    assert p.play(url, "x", local_buffer=live, buffer_key=key)
    return list(m.sets)

# With nothing of its own a channel plays exactly as any stream does: the
# same properties, the same values, in the same order.
plain = play("http://x/live/5.ts", key="live:5")
assert play("http://x/live/5.ts", "a:5", key="live:5") == plain
assert s.value(channel_prefs.STORE_KEY) is None
assert now(*GLOBAL) == GLOBAL, now(*GLOBAL)

store = channel_prefs.ChannelPrefs(s)
store.put("a:7", {"alang": "ger", "slang": "off", "deint": "on", "aspect": "4:3",
                  "adelay": 0.5})
play("http://x/live/7.ts", "a:7", key="live:7")
assert now(*GLOBAL) == {"alang": "ger,de,deu,swe", "deinterlace": True, "keepaspect": True,
                        "video-aspect-override": "4:3", "sid": "no", "audio-delay": 0.5}, now(*GLOBAL)
# A Settings save while it plays keeps the channel's own.
p.apply_default_options()
assert (m["alang"], m["deinterlace"], m["video-aspect-override"]) == ("ger,de,deu,swe", True, "4:3")
# The next channel - none of its own - gets the app's back, the delay too.
play("http://x/live/8.ts", "a:8", key="live:8")
assert now(*GLOBAL) == GLOBAL, now(*GLOBAL)
# So does anything that is no channel (nothing armed).
play("http://x/live/7.ts", "a:7", key="live:7")
play("http://x/movie/1.mkv", live=False, key="movie:1")
assert (m["alang"], m["deinterlace"], m["video-aspect-override"], m["audio-delay"]) == (
    "swe", False, "-1", 0.0)

# Its catch-up: the same picture and sound; subtitles as a film opens them.
store.put("a:7", {"slang": "eng", "aspect": "stretch"})
play("http://x/timeshift/7.ts", "a:7", live=False, key="live:7")
assert m["keepaspect"] is False and m["sub-visibility"] is False and m["sid"] == "auto"
assert m["slang"].startswith("eng,en"), m["slang"]
assert p._sub_rule == ("lang", ["eng", "en"]), p._sub_rule
m.track_list = [{"type": "sub", "id": 1, "lang": "eng", "selected": True}]
p._poll_position()
assert m["sub-visibility"] is True and p._sub_rule is None
m.track_list = None
# Live, a language shows at once.
play("http://x/live/7.ts", "a:7", key="live:7")
assert (m["sid"], m["sub-visibility"], m["slang"]) == ("auto", True, "eng,en")

# The delay chosen in the player is kept in the channel's settings.
p._remember_delay(0.25)
assert store.get("a:7") == {"slang": "eng", "aspect": "stretch", "adelay": 0.25}
# A channel's delay kept the old way moves in as it starts.
old = channel_prefs.legacy_key("live:9")
s.setValue(old, "-0.3")
play("http://x/live/9.ts", "a:9", key="live:9")
assert m["audio-delay"] == -0.3 and not s.contains(old) and store.get("a:9") == {"adelay": -0.3}
# Anything that is no channel keeps its delay as before, under a key of its own.
play("http://x/movie/1.mkv", live=False, key="movie:1")
p._remember_delay(0.1)
assert s.value(channel_prefs.legacy_key("movie:1")) == "0.1"
assert "movie" not in (s.value(channel_prefs.STORE_KEY) or "")

# The settings changed for the channel playing: the picture and the delay at once.
play("http://x/live/7.ts", "a:7", key="live:7")
store.put("a:7", {"deint": "on", "adelay": 1.0})
p.channel_prefs_changed("a:8")                      # another channel: nothing
assert m["deinterlace"] is False
p.channel_prefs_changed("a:7")
assert (m["deinterlace"], m["keepaspect"], m["audio-delay"]) == (True, True, 1.0)

# A damaged stored value never stops a channel: it plays with the app's.
s.setValue(channel_prefs.STORE_KEY, "{not json")
play("http://x/live/7.ts", "a:7", key="live:7")
assert now(*GLOBAL) == GLOBAL and p.current_url == "http://x/live/7.ts"

# Nerd off: kept, not applied - and the last channel's own delay is not left on.
store.put("a:7", {"alang": "ger", "deint": "on", "adelay": 0.5})
play("http://x/live/7.ts", "a:7", key="live:7")
assert m["audio-delay"] == 0.5
s.setValue("nerd_enabled", "false")
nerd.install(s)
play("http://x/live/7.ts", "a:7", key="live:7")
assert now(*GLOBAL) == GLOBAL, now(*GLOBAL)
assert store.get("a:7") == {"alang": "ger", "deint": "on", "adelay": 0.5}
# ... and with nothing to take back, Nerd off leaves the delay alone, as before.
m.sets.clear()
play("http://x/live/8.ts", "a:8", key="live:8")
assert "audio-delay" not in dict(m.sets)
print("PLAYER_PREFS_OK", flush=True)
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


def test_the_player_applies_a_channels_own_and_gives_the_next_the_apps(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    out = subprocess.run([sys.executable, "-c", _PLAYER_CHILD], cwd=_REPO_ROOT,
                         env=_child_env(tmp_path), capture_output=True, text=True, timeout=180)
    assert "PLAYER_PREFS_OK" in out.stdout, out.stdout + out.stderr[-3000:]


# -- in the window: the menu, the dialog and the playback path -----------------------------

_WINDOW_CHILD = r"""
import json, os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication, QMenu
app = QApplication.instance() or QApplication([])

from dopeiptv.core import channel_prefs, nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw


class Client(DemoClient):
    def live_url(self, stream_id, fmt="ts"):
        return f"http://127.0.0.1:9/{stream_id}.ts"


def external(*_a, **_k):
    raise AssertionError("an external player was started")


class FakeMpv(dict):
    pause = False

    def command(self, *args):
        pass

    def play(self, url):
        self.url = url

    def __getattr__(self, n):
        return None


mw.launch_player = external
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "a", "name": "Main", "kind": "m3u",
                                     "server": "http://127.0.0.1:9/a.m3u"}]))
s.setValue("active_playlist", "a")
s.setValue("audio_lang", "swe")
s.setValue("nerd_enabled", "true")
old = channel_prefs.legacy_key("live:7")
s.setValue(old, "0.25")                       # kept the old way, before
nerd.install(s)
w = mw.MainWindow(Client(), s, PlaylistStore(s))
if w.player is None:
    print("PREFS_SKIP: no embedded player here")
    sys.exit(0)
m = w.player.video.mpv = FakeMpv()
w.player.isVisible = lambda: True
w.wake.acquire = w.wake.release = lambda *a, **k: None
toasts = []
w._show_toast = lambda text, ms=0: toasts.append(text)
tv4 = {"name": "TV4 HD", "stream_id": 7, "tv_archive": 1, "tv_archive_duration": 3}
k8 = {"name": "Kanal 8", "stream_id": 8}


def entry(it):
    # The menu's entry for it: None, "own" (marked) or "plain".
    menu = QMenu()
    w._chanprefs_menu(menu, it)
    acts = [a for a in menu.actions() if a.text() == tr("ctx_channel_settings")]
    return None if not acts else ("plain" if acts[0].icon().isNull() else "own")


# The channel's menu: the entry while Nerd is on; the old delay already
# makes it a channel with settings of its own - moved in as it is read.
assert entry(tv4) == "own"
assert not s.contains(old) and channel_prefs.ChannelPrefs(s).get("a:7") == {"adelay": 0.25}
assert entry(k8) == "plain"

# The dialog: what the channel has, chosen, saved.
d = w._channel_settings_dialog(tv4)
assert d.delay.value() == 250 and d.marker.text().endswith(tr("chanprefs_own"))
d.alang.setCurrentIndex(d.alang.findData("ger"))
d.deint.setCurrentIndex(d.deint.findData("on"))
d.aspect.setCurrentIndex(d.aspect.findData("21:9"))
w._chanprefs_save(tv4, d.choice())
assert channel_prefs.ChannelPrefs(s).get("a:7") == {
    "alang": "ger", "deint": "on", "aspect": "21:9", "adelay": 0.25}
assert toasts[-1] == tr("chanprefs_saved", name="TV4 HD")

# It starts: the channel's own. The next channel: the app's again.
w._start_playback("http://127.0.0.1:9/7.ts", "TV4 HD", None, 7, "live", item=tv4)
assert m.url == "http://127.0.0.1:9/7.ts"
assert (m["alang"], m["deinterlace"], m["video-aspect-override"], m["audio-delay"]) == (
    "ger,de,deu,swe", True, "21:9", 0.25), dict(m)
w._start_playback("http://127.0.0.1:9/8.ts", "Kanal 8", None, 8, "live", item=k8)
assert (m["alang"], m["deinterlace"], m["video-aspect-override"], m["audio-delay"]) == (
    "swe", False, "-1", 0.0), dict(m)
# Its catch-up plays with them too.
w._start_playback("http://127.0.0.1:9/timeshift/7.ts", "TV4 HD", None, 7, "live",
                  record=False, item=tv4, catchup=True)
assert (m["alang"], m["deinterlace"], m["video-aspect-override"]) == ("ger,de,deu,swe", True, "21:9")
# A film is no channel.
w._start_playback("http://127.0.0.1:9/movie/1.mkv", "Film", None, 1, "movie",
                  item={"name": "Film", "stream_id": 1})
assert (m["alang"], m["deinterlace"]) == ("swe", False)

# Changed while it plays: in force at once.
w._start_playback("http://127.0.0.1:9/7.ts", "TV4 HD", None, 7, "live", item=tv4)
w._chanprefs_save(tv4, {"alang": "ger", "deint": "off", "aspect": "21:9", "adelay": 0.25})
assert m["deinterlace"] is False

# Nerd off: no entry, nothing applied, everything kept.
s.setValue("nerd_enabled", "false")
nerd.install(s)
assert entry(tv4) is None
w._start_playback("http://127.0.0.1:9/7.ts", "TV4 HD", None, 7, "live", item=tv4)
assert (m["alang"], m["deinterlace"], m["video-aspect-override"]) == ("swe", False, "-1")
assert channel_prefs.ChannelPrefs(s).get("a:7")["alang"] == "ger"

# On again: Reset puts the channel back to the app's settings.
s.setValue("nerd_enabled", "true")
nerd.install(s)
d = w._channel_settings_dialog(tv4)
d.reset()
w._chanprefs_save(tv4, d.choice())
assert channel_prefs.ChannelPrefs(s).get("a:7") == {} and s.value(channel_prefs.STORE_KEY) is None
assert toasts[-1] == tr("chanprefs_cleared", name="TV4 HD")
assert entry(tv4) == "plain"
print("WINDOW_PREFS_OK", flush=True)
os._exit(0)
"""


def test_the_window_offers_saves_and_plays_a_channels_own(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    out = subprocess.run([sys.executable, "-c", _WINDOW_CHILD], cwd=_REPO_ROOT,
                         env=_child_env(tmp_path), capture_output=True, text=True, timeout=180)
    if "PREFS_SKIP" in out.stdout:
        pytest.skip(out.stdout.strip())
    assert "WINDOW_PREFS_OK" in out.stdout, out.stdout + out.stderr[-3000:]
