"""The playback path (MainWindow._start_playback) as it is, step by step -
written before it was cut into named parts, so the cut changes nothing.

Every way something starts to play - a channel, the next channel, a film,
an episode, a local file, a station, a web stream, a recording, the
archive, an address opened by hand, one the player will not open, one
only an external player takes - runs in a real window, and each of the
window's hooks the path calls, the player's and the stores', is noted in
the order it is called (only those the path itself calls, not what they
call in turn). The order is the behaviour: what is left before the next
starts, what is kept, who is told, what the player is handed, what follows
when it opens or does not.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# -- the path's steps, each as it is called -------------------------------------------------

GUARDS = ["_kids_may_play", "_guard_stream_switch", "_vpn_guard_blocks", "_cast_zap"]
GUARDS_OWN_FILE = ["_kids_may_play", "_guard_stream_switch", "_cast_zap"]   # no VPN guard
LEAVE = ["_failover_leave", "_stop_cast_for_local_playback", "_maybe_close_multiview_for_playback",
         "_save_resume_position", "_maybe_auto_mark_watched", "_ms_leave", "_together_take_start",
         "_sp_take_start"]
RESUME = ["_resume_offset"]                      # a film, an episode, a file, a recording


def note(record=True, leaving_live=False, episode=False):
    return (["_trakt_stop_current", "_simkl_stop_current"]
            + (["_history_extra", "history.add"] if record else [])
            + ["_watch_accounts_start", "stream_error.hide"]
            + (["_item_key"] if leaving_live else [])
            + ["_note_playing_source"]
            + (["_has_next_episode"] if episode else [])
            + ["player.set_next_available", "_sync_mark_play", "_sync_player_buttons"])


def tell(episode=False):
    return (["_sp_playback_started"] + (["_detail_follow_episode"] if episode else [])
            + ["_pf_playback_started", "_place_series_playing", "_place_local_playing",
               "_place_rec_playing", "_place_radio_playing", "_pane_follow_soon",
               "_place_tv_played", "setWindowTitle", "_status_playing", "_set_status"])


def hand_over(local=False, resumable=False, by_hand=False):
    return (["rec.finish_all_inplayer", "player.show", "player.set_overlay_info",
             "_apply_audio_visuals"]
            + (["_is_audio", "_show_detail", "_place_in_queue", "_sync_queue_buttons"] if local else [])
            + (["_track_prefs", "_series_pref_keys", "_remembered_subtitles", "player.set_sub_files"]
               if resumable else [])
            + ["_chanprefs_arm", "_tv_before_play", "_arm_loading"]
            + (["_from_outside"] if by_hand else [])
            + ["player.play"])


def opened(catchup=False):
    return (["wake.acquire", "_health_begin", "_events_started", "_together_started",
             "_stats_begin", "_discord_started"] + ([] if catchup else ["_ms_started"])
            + ["_tv_started"])


END = ["_apply_play_icon", "_apply_seek_mode"]

EXPECTED = {
    "live": GUARDS + LEAVE + note() + tell() + hand_over() + opened() + END,
    "live_next": GUARDS + LEAVE + note(leaving_live=True) + tell() + hand_over() + opened() + END,
    "movie": GUARDS + LEAVE + RESUME + note() + tell() + hand_over(resumable=True) + opened() + END,
    "episode": (GUARDS + LEAVE + RESUME + note(episode=True) + tell(episode=True)
                + hand_over(resumable=True) + opened() + END),
    "local": (GUARDS_OWN_FILE + LEAVE + RESUME + note() + tell()
              + hand_over(local=True, resumable=True) + opened() + END),
    "radio": GUARDS + LEAVE + note() + tell() + hand_over() + opened() + END,
    "web": GUARDS + LEAVE + note() + tell() + hand_over() + opened() + END,
    "recording": (GUARDS_OWN_FILE + LEAVE + RESUME + note() + tell() + hand_over(resumable=True)
                  + opened() + END),
    "catchup": GUARDS + LEAVE + note(record=False) + tell() + hand_over() + opened(catchup=True) + END,
    "by_hand": (GUARDS + LEAVE + note(leaving_live=True) + tell() + hand_over(by_hand=True)
                + opened() + END),
    # Not opened on purpose (a certificate it cannot check): said on the video.
    "refused": GUARDS + LEAVE + note() + tell() + hand_over() + ["_show_stream_error"] + END,
    # Not opened at all: an external mpv, the docked player hidden.
    "external": (GUARDS + LEAVE + note(leaving_live=True) + tell() + hand_over()
                 + ["player.hide", "launch_player"] + END),
    # A kids profile's lists do not show it: nothing at all.
    "kids_no": ["_kids_may_play"],
    # A cast runs: the channel goes to the TV, nothing here changes.
    "cast_zap": GUARDS,
}

#: What the player is handed: where to start, the app's own rewind buffer,
#: its key, a web page through yt-dlp, the source's own buffer, an address
#: from outside the source (none of its headers).
PLAYS = {
    "live": (0.0, True, "live:1", False, True, False),
    "live_next": (0.0, True, "live:2", False, True, False),
    "movie": (0.0, False, "movie:9001", False, True, False),
    "episode": (0.0, False, "episode:501", False, True, False),
    "radio": (0.0, False, "radio:st-1", False, True, True),
    "web": (0.0, False, "web:https://www.youtube.com/watch?v=x", True, True, False),
    "catchup": (30.0, False, "live:1", False, True, False),
    "by_hand": (0.0, True, "live:http://192.0.2.5/x.ts", False, False, True),
    "refused": (0.0, True, "live:1", False, True, False),
    "external": (0.0, True, "live:2", False, True, False),
}

#: What the window keeps of it: the key, its group, the archive or not.
STATE = {
    "live": ("1", "live", False), "live_next": ("2", "live", False),
    "movie": ("9001", "vod", False), "episode": ("501", "episode", False),
    "radio": ("st-1", "radio", False), "web": ("https://www.youtube.com/watch?v=x", "web", False),
    "catchup": ("1", "live", True), "by_hand": ("http://192.0.2.5/x.ts", "live", False),
    "refused": ("1", "live", False), "external": ("2", "live", False),
    # Refused before anything: what played before is still what is kept.
    "kids_no": ("2", "live", False), "cast_zap": ("2", "live", False),
}

_CHILD = r"""
import json, os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw

HOOKS = ['_kids_may_play', '_guard_stream_switch', '_vpn_guard_blocks', '_cast_zap',
         '_failover_leave', '_stop_cast_for_local_playback', '_maybe_close_multiview_for_playback',
         '_save_resume_position', '_maybe_auto_mark_watched', '_ms_leave', '_together_take_start',
         '_sp_take_start', '_resume_offset', '_trakt_stop_current', '_simkl_stop_current',
         '_history_extra', '_watch_accounts_start', '_item_key', '_note_playing_source',
         '_has_next_episode', '_sync_mark_play', '_sync_player_buttons', '_sp_playback_started',
         '_detail_follow_episode', '_pf_playback_started', '_place_series_playing',
         '_place_local_playing', '_place_rec_playing', '_place_radio_playing', '_pane_follow_soon',
         '_place_tv_played', 'setWindowTitle', '_status_playing', '_set_status',
         '_apply_audio_visuals', '_is_audio', '_show_detail', '_place_in_queue',
         '_sync_queue_buttons', '_track_prefs', '_series_pref_keys', '_remembered_subtitles',
         '_chanprefs_arm', '_tv_before_play', '_arm_loading', '_from_outside', '_health_begin',
         '_events_started', '_together_started', '_stats_begin', '_discord_started',
         '_ms_started', '_tv_started', '_show_stream_error', '_tv_refused', '_apply_play_icon',
         '_apply_seek_mode', '_verify_catchup']

s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
w = mw.MainWindow(DemoClient(), s, PlaylistStore(s))
if w.player is None:
    print("SKIP: no embedded player here")
    sys.exit(0)
w.show()
for _ in range(20):
    app.processEvents()

events: list = []
depth = [0]


def wrap(name, fn):
    def inner(*a, **k):
        if depth[0] == 0:
            events.append(name)
        depth[0] += 1
        try:
            return fn(*a, **k)
        finally:
            depth[0] -= 1
    return inner


for name in HOOKS:
    fn = getattr(w, name, None)
    if callable(fn):
        setattr(w, name, wrap(name, fn))

player = w.player
plays: list = []
result = {"open": True, "refusal": ""}


def play(url, title, *a, **k):
    events.append("player.play")
    plays.append({"url": url, "title": title, **{x: k.get(x) for x in
                  ("start", "local_buffer", "buffer_key", "web", "own_buffer", "outside")}})
    player.refusal = result["refusal"]
    return result["open"]


player.play = play
for name in ("show", "hide", "set_overlay_info", "set_next_available", "set_track_prefs",
             "set_sub_files"):
    setattr(player, name, wrap("player." + name, getattr(player, name)))
w.history.add = wrap("history.add", w.history.add)
w.rec.finish_all_inplayer = wrap("rec.finish_all_inplayer", w.rec.finish_all_inplayer)
w.wake.acquire = lambda *a, **k: events.append("wake.acquire")
w.wake.release = lambda *a, **k: None
mw.launch_player = lambda *a, **k: events.append("launch_player")
w.stream_error.hide = wrap("stream_error.hide", w.stream_error.hide)
real_start = w._start_playback

out = {}


def run(label, *a, **k):
    events.clear()
    plays.clear()
    real_start(*a, **k)
    sync = list(events)
    last = dict(getattr(w, "_last_playback", None) or {})
    last.pop("item", None)
    last.pop("series_ctx", None)
    last.pop("ep_queue", None)
    out[label] = {"events": sync, "plays": list(plays),
                  "key": None if w._playing_key is None else str(w._playing_key),
                  "group": w._playing_group, "catchup": bool(w._playing_catchup),
                  "last": {k2: v for k2, v in sorted(last.items())},
                  "title": w.windowTitle()}


one = {"name": "One", "stream_id": 1, "category_id": "1"}
two = {"name": "Two", "stream_id": 2, "category_id": "1"}
run("live", "http://192.0.2.1/live/u/p/1.ts", "One", None, 1, "live", item=one)
run("live_next", "http://192.0.2.1/live/u/p/2.ts", "Two", None, 2, "live", item=two)
film = {"name": "Film", "stream_id": 9001, "container_extension": "mp4"}
run("movie", "http://192.0.2.1/movie/u/p/9001.mp4", "Film", None, 9001, "movie", item=film)
e1 = {"id": "501", "title": "S1 E1", "episode_num": 1, "season": 1, "container_extension": "mkv"}
e2 = {"id": "502", "title": "S1 E2", "episode_num": 2, "season": 1, "container_extension": "mkv"}
w.series_ctx = {"series_id": 77, "name": "Show"}
w.all_items = [e1, e2]
run("episode", "http://192.0.2.1/series/u/p/501.mkv", "S1 E1", None, "501", "episode", item=e1)
w.series_ctx = None
song = tempfile.mkstemp(suffix=".mp3")[1]
run("local", song, "song", None, song, "local", item={"name": "song", "path": song})
run("radio", "http://192.0.2.9/stream.mp3", "Station", None, "st-1", "radio",
    item={"name": "Station", "stationuuid": "st-1"})
run("web", "https://www.youtube.com/watch?v=x", "Clip", None, "https://www.youtube.com/watch?v=x",
    "web")
rec = tempfile.mkstemp(suffix=".ts")[1]
run("recording", rec, "Recorded", None, rec, "recording", item={"name": "Recorded", "path": rec})
run("catchup", "http://192.0.2.1/timeshift/u/p/60/2026-01-01:20-00/1.ts", "One", None, 1, "live",
    item=one, catchup=True, start_at=30.0, record=False)
run("by_hand", "http://192.0.2.5/x.ts", "x", None, "http://192.0.2.5/x.ts", "live",
    own_buffer=False)
result.update(open=False, refusal="unchecked certificate")
run("refused", "http://192.0.2.1/live/u/p/1.ts", "One", None, 1, "live", item=one)
result.update(open=False, refusal="")
run("external", "http://192.0.2.1/live/u/p/2.ts", "Two", None, 2, "live", item=two)
result.update(open=True, refusal="")
w._kids_may_play = wrap("_kids_may_play", lambda kind, item, key=None: False)
run("kids_no", "http://192.0.2.1/live/u/p/1.ts", "One", None, 1, "live", item=one)
w._kids_may_play = wrap("_kids_may_play", lambda kind, item, key=None: True)
w._cast_zap = wrap("_cast_zap", lambda *a, **k: True)
run("cast_zap", "http://192.0.2.1/live/u/p/1.ts", "One", None, 1, "live", item=one)
print("RESULT" + json.dumps(out), flush=True)
os._exit(0)
"""


@pytest.fixture(scope="module")
def seen(tmp_path_factory):
    pytest.importorskip("PyQt6.QtWidgets")
    home = tmp_path_factory.mktemp("playback-path")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(home / var.lower())
    out = subprocess.run([sys.executable, "-c", _CHILD],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    if out.stdout.startswith("SKIP"):
        pytest.skip(out.stdout.strip())
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, out.stdout[-2000:] + out.stderr[-4000:]
    return json.loads(line[6:])


@pytest.mark.parametrize("way", sorted(EXPECTED))
def test_each_step_in_its_order(seen, way):
    got = seen[way]["events"]
    want = EXPECTED[way]
    assert got == want, (way, [x for x in got if x not in want], [x for x in want if x not in got])


@pytest.mark.parametrize("way", sorted(PLAYS))
def test_what_the_player_is_handed(seen, way):
    (p,) = seen[way]["plays"]
    assert (p["start"], p["local_buffer"], p["buffer_key"], p["web"], p["own_buffer"],
            p["outside"]) == PLAYS[way], p


def test_a_file_and_a_recording_are_handed_as_they_are(seen):
    for way in ("local", "recording"):
        (p,) = seen[way]["plays"]
        assert p["buffer_key"] == f"{way}:{p['url']}" and p["start"] == 0.0
        assert not p["local_buffer"] and not p["web"] and p["own_buffer"] and not p["outside"]
        assert seen[way]["key"] == p["url"]
    assert seen["local"]["group"] == "local" and seen["recording"]["group"] == "rec"


@pytest.mark.parametrize("way", sorted(STATE))
def test_what_the_window_keeps(seen, way):
    s = seen[way]
    assert (s["key"], s["group"], s["catchup"]) == STATE[way], s


def test_what_a_stop_and_play_replays_and_the_episode_queue(seen):
    assert seen["live"]["last"] == {"icon_url": None, "key": 1, "kind": "live", "own_buffer": True,
                                    "title": "One", "url": "http://192.0.2.1/live/u/p/1.ts"}
    assert seen["by_hand"]["last"]["own_buffer"] is False
    assert seen["episode"]["last"]["ep_index"] == 0           # the series' first of two
    assert seen["catchup"]["last"]["url"].startswith("http://192.0.2.1/timeshift/")
    for way in ("kids_no", "cast_zap"):                      # nothing new kept
        assert seen[way]["last"]["key"] == 2
    assert seen["movie"]["title"] == "Film" and seen["cast_zap"]["title"] == "Two"
