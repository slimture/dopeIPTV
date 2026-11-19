"""An address from outside the source's lists - a dopeiptv:// link, --play,
the remote API's url, Open address… - is played, and cast, with none of the
source's Nerd settings (its headers often hold the provider's token): only
an address on the source's own server gets them (core/nerd.same_server)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dopeiptv.core import nerd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_only_the_source_s_own_server_is_the_source_s():
    server = "http://Provider.example:8080"
    assert nerd.same_server("http://provider.example:8080/live/u/p/1.ts", server)
    assert nerd.same_server("https://provider.example./x.m3u8", server)
    for other in ("https://attacker.example/a.ts", "http://provider.example.attacker.example/a",
                  "http://evil@attacker.example/", "rtsp://10.0.0.5/x", "", "not a url"):
        assert not nerd.same_server(other, server), other
    assert not nerd.same_server("http://provider.example/x", "")
    assert not nerd.same_server("http://[::1/x", server)


def test_a_cast_of_an_outside_address_takes_no_source_s_settings():
    from dopeiptv.ui.mw_cast import _CastMixin
    pl = {"id": "p1", "kind": "xtream", "server": "http://provider.example",
          "options": {"headers": "X-Auth: secret-token", "user_agent": "SourceUA/1"}}
    nerd.set_source(pl)
    try:
        opened: list = []
        stub = SimpleNamespace(
            player=SimpleNamespace(current_url="https://attacker.example/a.ts"),
            _playing_item=None, _detail_name="a",
            _last_playback={"url": "https://attacker.example/a.ts", "own_buffer": False},
            _conn_player_source=lambda: "p1", _cast_source_of=lambda *_a: "p1",
            _from_outside=lambda url: not nerd.same_server(url, pl["server"]),
            _open_cast_dialog=lambda it, src: opened.append((it, src)))
        _CastMixin.cast_playing(stub)
        (it, src), = opened
        assert it["_outside"] is True and src == "p1"
        stub._last_playback = {"url": "https://attacker.example/a.ts", "own_buffer": True}
        _CastMixin.cast_playing(stub)
        assert "_outside" not in opened[-1][0], "a source's own address as before"

        cast = SimpleNamespace(_cast_playlist=lambda _sid="": pl)
        _CastMixin._cast_route_from(cast, "p1", outside=True)
        assert nerd.cast_source() == {}
        _CastMixin._cast_route_from(cast, "p1")
        assert nerd.cast_source()["headers"] == "X-Auth: secret-token"
    finally:
        nerd.set_source(None)
        nerd.set_cast_source(None)


_CHILD = r"""
import json, os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv.core import nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw

mw.launch_player = lambda *a, **k: (_ for _ in ()).throw(AssertionError("external player"))
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("nerd_enabled", "true")
s.setValue("live_rewind_minutes", 0)
store = PlaylistStore(s)
pl = store.add({"id": "p1", "name": "P", "kind": "xtream", "server": "http://127.0.0.1:9",
                "username": "u", "password": "p",
                "options": {"headers": "X-Auth: secret-token", "user_agent": "SourceUA/1",
                            "referer": "http://127.0.0.1:9/portal"}})
store.set_active("p1")
nerd.install(s)
w = mw.MainWindow(DemoClient(), s, store)
nerd.set_source(store.active())
if w.player is None:
    print("SKIP: no embedded player here")
    sys.exit(0)
player = w.player
real_play = player.play
plays = []
player.play = lambda url, title, *a, **k: plays.append([url, k.get("outside")]) or True
player.isVisible = lambda: True
w.wake.acquire = w.wake.release = lambda *a, **k: None

w._play_address("http://127.0.0.2:9/a.ts")          # another host: a link, --play
w._diag_shown = False
w._retry_last_stream()                              # its reconnect
w._play_address("http://127.0.0.1:9/live/u/p/1.ts") # the source's own server
w._start_playback("http://127.0.0.1:9/7.ts", "TV4", None, 7, "live",
                  item={"name": "TV4", "stream_id": 7})

player.play = real_play
player._dvr_start = lambda url, key=None: None
try:
    import locale
    import mpv
    locale.setlocale(locale.LC_NUMERIC, "C")
    m = player.video.mpv = mpv.MPV(vo="null", ao="null", terminal=False, idle=True)
except Exception as e:
    print("SKIP: no libmpv core here (%s)" % e)
    sys.exit(0)
seen = {}
for outside in (True, False):
    player.play("http://127.0.0.2:9/b.ts", "b", own_buffer=False, outside=outside)
    seen[str(outside)] = [list(m["http-header-fields"] or []), str(m["user-agent"]),
                          str(m["referrer"] or "")]
print("RESULT" + json.dumps({"plays": plays, "seen": seen}), flush=True)
os._exit(0)
"""


def test_an_outside_address_never_gets_the_source_s_headers(tmp_path):
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
    got = json.loads(line[6:])
    assert got["plays"] == [["http://127.0.0.2:9/a.ts", True],
                            ["http://127.0.0.2:9/a.ts", True],
                            ["http://127.0.0.1:9/live/u/p/1.ts", False],
                            ["http://127.0.0.1:9/7.ts", False]], got
    headers, agent, referrer = got["seen"]["True"]
    assert not any("secret-token" in h for h in headers), headers
    assert agent != "SourceUA/1" and "127.0.0.1:9/portal" not in referrer
    headers, agent, referrer = got["seen"]["False"]
    assert "X-Auth: secret-token" in headers and agent == "SourceUA/1", got["seen"]
