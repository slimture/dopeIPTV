"""An address opened by hand (File > Open address…, --play, the remote API)
is no provider's channel and need not be a stream at all - a tester opened
a web page, and with dopeIPTV's own rewind buffer on the buffer asked for it
twice a second for ever. mpv opens such an address itself, and says at once
what it found; YouTube and the like still go through yt-dlp, a channel of a
source still gets the buffer. A reconnect, or Play after Stop, opens it the
way it was opened."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dopeiptv.ui.main_window import MainWindow

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _host(last: dict) -> tuple[SimpleNamespace, list]:
    calls: list = []
    h = SimpleNamespace(
        _diag_shown=False, _playing_catchup=False, _last_playback=last,
        _health_retry=lambda: None, client=None,
        play_live_channel=lambda it: calls.append(("channel", it)),
        _start_playback=lambda *a, **k: calls.append((a, k)))
    return h, calls


def test_a_reconnect_opens_an_address_by_hand_as_it_was_opened():
    h, calls = _host({"url": "http://h/page", "title": "page", "key": "http://h/page",
                      "kind": "live", "item": None, "own_buffer": False})
    MainWindow._retry_last_stream(h)
    (args, kw), = calls
    assert args[0] == "http://h/page" and args[4] == "live"
    assert kw == {"record": False, "own_buffer": False}


def test_a_reconnect_from_history_is_as_before():
    # Replayed from History (an address, no channel row): the buffer, and
    # kept in Recently viewed, as it always was.
    h, calls = _host({"url": "http://h/live/1.ts", "title": "One", "key": "k",
                      "kind": "live", "item": None, "own_buffer": True})
    MainWindow._retry_last_stream(h)
    (args, kw), = calls
    assert args[0] == "http://h/live/1.ts" and kw == {}
    # Older playback state, without the key at all: the same.
    h, calls = _host({"url": "http://h/live/1.ts", "title": "One", "key": "k",
                      "kind": "live", "item": None})
    MainWindow._retry_last_stream(h)
    assert calls[0][1] == {}


def test_play_after_stop_opens_it_the_same_way():
    for own in (False, True):
        h, calls = _host({"url": "http://h/x", "title": "x", "icon_url": None, "key": "x",
                          "kind": "live", "item": None, "own_buffer": own})
        MainWindow._resume_last(h)
        assert calls[0][1]["own_buffer"] is own and calls[0][1]["to_cast"] is False


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

mw.launch_player = lambda *a, **k: (_ for _ in ()).throw(AssertionError("external player"))
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("live_rewind_kept", "true")
w = mw.MainWindow(DemoClient(), s, PlaylistStore(s))
if w.player is None:
    print("SKIP: no embedded player here")
    sys.exit(0)
player = w.player
real_play = player.play
plays = []
player.play = lambda url, title, *a, **k: plays.append(
    [url, k.get("local_buffer"), k.get("own_buffer", True)]) or True
player.isVisible = lambda: True
w.wake.acquire = w.wake.release = lambda *a, **k: None

w._play_address("http://127.0.0.1:9/stable")
w._diag_shown = False
w._retry_last_stream()                      # the app's reconnect
w._start_playback("http://127.0.0.1:9/7.ts", "TV4", None, 7, "live",
                  item={"name": "TV4", "stream_id": 7})
kept = [e.get("_url") or e.get("url") for e in w.history.entries]

# The player: the app's own buffer only for what may have it.
player.play = real_play
asked = []
player._dvr_start = lambda url, key=None: asked.append(url) or None
for own in (False, True):
    try:
        player.play("http://127.0.0.1:9/x.ts", "x", local_buffer=True,
                    buffer_key="live:x", own_buffer=own)
    except Exception as e:
        print("play raised", type(e).__name__, e)
print("RESULT" + json.dumps({"plays": plays, "kept": kept, "asked": asked}), flush=True)
os._exit(0)
"""


def test_the_window_opens_an_address_by_hand_without_the_buffer(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    if out.stdout.startswith("SKIP"):
        pytest.skip(out.stdout.strip())
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, out.stdout + out.stderr[-3000:]
    import json
    got = json.loads(line[6:])
    # Opened by hand, and again by the reconnect: a live stream to mpv (its
    # own rewind), never dopeIPTV's buffer; a source's channel: the buffer.
    assert got["plays"] == [["http://127.0.0.1:9/stable", True, False],
                            ["http://127.0.0.1:9/stable", True, False],
                            ["http://127.0.0.1:9/7.ts", True, True]], got
    assert "http://127.0.0.1:9/stable" not in got["kept"], "kept in Recently viewed"
    assert got["asked"] == ["http://127.0.0.1:9/x.ts"], got
