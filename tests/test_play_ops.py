"""What the player tells mpv, word for word - written before
EmbeddedPlayer.play() was cut into steps, so the cut changes nothing.

A real EmbeddedPlayer with a stand-in mpv that notes every property set,
command and load in its order plays: a live channel with mpv's own
rewind, one from the app's rewind buffer, a film from where it got to, a
film with its tracks and a subtitle kept, a channel with subtitles off, a
web page (its own file options), a channel after it, an RTSP address of
no SAT>IP source (played as it is), a channel while muted, an address that is no stream, a
stream whose certificate this core will not check, and no core at all.

tests/playback/play_ops.json holds what mpv was told. When a change to it
is meant - a new option, another order - write it again with
DOPEIPTV_WRITE_PLAY_OPS=1 and read the diff: every line of it reaches
every stream that plays.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KEPT = ROOT / "tests" / "playback" / "play_ops.json"

_CHILD = r"""
import json, os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("DOPEIPTV_HWDEC", None)
from PyQt6.QtCore import QSettings
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv.media import embedded
from dopeiptv.media.embedded import EmbeddedPlayer

ops: list = []


import re

_BRIDGE = re.compile(r"http://dopeiptv:[^@/]+@127\.0\.0\.1:\d+")


def norm(v):
    # What differs from run to run: a float's last digits, the temporary
    # folder, the app's bridge's own key and port.
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, str):
        return _BRIDGE.sub("@BRIDGE@", v.replace(tmp, "@TMP@"))
    return v


class FakeMpv(dict):
    def __init__(self):
        super().__init__()
        object.__setattr__(self, "_pause", False)

    def __setitem__(self, k, v):
        # The certificate bundle is wherever this machine keeps it (the
        # system's, or certifi's): that one is set is what counts.
        ops.append(["set", k, "@CA@" if k == "tls-ca-file" and v else norm(v)])
        super().__setitem__(k, v)

    def __getattr__(self, n):
        if n == "pause":
            return object.__getattribute__(self, "_pause")
        return None

    def __setattr__(self, n, v):
        if n == "pause":
            ops.append(["pause", v])
            object.__setattr__(self, "_pause", v)
        else:
            object.__setattr__(self, n, v)

    def command(self, *a):
        ops.append(["command"] + [norm(x) for x in a])

    def play(self, url):
        ops.append(["play", url])


p = EmbeddedPlayer()
p.video.mpv = FakeMpv()
p.vol.setValue(70)
dvr = {"url": None}
p._dvr_start = lambda url, key=None: dvr["url"]

out = {}


def run(label, url, title, start=0.0, before=None, **kw):
    ops.clear()
    if before is not None:
        before()
    ok = p.play(url, title, start, **kw)
    out[label] = {"ok": ok, "ops": list(ops), "refusal": p.refusal,
                  "current": p.current_url, "buffer": bool(p._local_buffer_enabled),
                  "args": [p._play_args[0], p._play_args[2], p._play_args[3]]
                  if getattr(p, "_play_args", None) else None}


run("live", "http://192.0.2.1/live/u/p/1.ts", "One", local_buffer=True,
    buffer_key="live:1", before=lambda: p._settings.setValue("live_rewind_minutes", 0)
    if p._settings is not None else None)
dvr["url"] = "http://127.0.0.1:41000/live.ts?t=x"
run("live_buffer", "http://192.0.2.1/live/u/p/2.ts", "Two", local_buffer=True,
    buffer_key="live:2", before=lambda: p._settings.setValue("live_rewind_minutes", 30)
    if p._settings is not None else None)
dvr["url"] = None
run("movie_resume", "http://192.0.2.1/movie/u/p/9.mp4", "Film", 600.0, buffer_key="movie:9")


def prefs():
    p.set_track_prefs(2, 3)
    sub = os.path.join(tmp, "film.srt")
    open(sub, "w").write("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    p.set_sub_files([sub])


run("tracks", "http://192.0.2.1/movie/u/p/9.mp4", "Film", 0.0, buffer_key="movie:9",
    before=prefs)
run("subs_off_live", "http://192.0.2.1/live/u/p/1.ts", "One", local_buffer=True,
    buffer_key="live:1", before=lambda: p.set_track_prefs(None, "no"))
run("web", "https://www.youtube.com/watch?v=x", "Clip", web=True,
    buffer_key="web:https://www.youtube.com/watch?v=x")
run("after_web", "http://192.0.2.1/live/u/p/1.ts", "One", local_buffer=True, buffer_key="live:1")
run("rtsp", "rtsp://192.168.1.5:554/?src=1&freq=11494", "SAT", local_buffer=False,
    buffer_key="live:sat")


def mute():
    if not p._muted:
        p.toggle_mute()


run("muted", "http://192.0.2.1/live/u/p/1.ts", "One", local_buffer=True, buffer_key="live:1",
    before=mute)
run("no_stream", "av://lavfi:anullsrc", "x")


def unchecked():
    from dopeiptv.core import nerd
    real = nerd.apply_stream

    def boom(*a, **k):
        raise embedded.CertificateUnchecked("refused")
    nerd.apply_stream = boom
    unchecked.real = real


run("unchecked", "https://example.com/live/1.m3u8", "TLS", local_buffer=True,
    buffer_key="live:tls", before=unchecked)
from dopeiptv.core import nerd  # noqa: E402
nerd.apply_stream = unchecked.real
saved = p.video.mpv
p.video.mpv = None
p.video.ensure_started = None
run("no_core", "http://192.0.2.1/live/u/p/1.ts", "One", local_buffer=True, buffer_key="live:1")
p.video.mpv = saved
print("RESULT" + json.dumps(out), flush=True)
os._exit(0)
"""


@pytest.fixture(scope="module")
def told(tmp_path_factory):
    pytest.importorskip("PyQt6.QtWidgets")
    home = tmp_path_factory.mktemp("play-ops")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("DOPEIPTV_HWDEC", None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                "XDG_RUNTIME_DIR"):
        (home / var.lower()).mkdir(mode=0o700)
        env[var] = str(home / var.lower())
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=300)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, out.stdout[-2000:] + out.stderr[-4000:]
    got = json.loads(line[6:])
    if os.environ.get("DOPEIPTV_WRITE_PLAY_OPS") == "1":
        KEPT.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return got


def kept() -> dict:
    return json.loads(KEPT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("way", sorted(json.loads(KEPT.read_text(encoding="utf-8"))))
def test_mpv_is_told_what_it_was_told(told, way):
    want, got = kept()[way], told[way]
    assert got["ops"] == want["ops"], (way, "\n".join(
        f"{i}: {a} | {b}" for i, (a, b) in enumerate(zip(got["ops"] + [None] * 40,
                                                           want["ops"] + [None] * 40,
                                                           strict=False))
        if a != b)[:3000])
    for k in ("ok", "refusal", "current", "buffer", "args"):
        assert got[k] == want[k], (way, k, got[k], want[k])


def test_what_the_record_says_holds():
    """The record itself, read for what matters, so a rewrite cannot keep
    a wrong one: each stream unpaused after its load, the app's buffer
    played from this computer, a web page by its own file options, an address that is no stream and an unchecked certificate
    never loaded."""
    k = kept()
    for way in ("live", "live_buffer", "movie_resume", "tracks", "web", "rtsp", "muted"):
        ops = k[way]["ops"]
        assert ops[-1] == ["pause", False], way
        loads = [o for o in ops if o[0] == "play" or (o[0] == "command" and o[1] == "loadfile")]
        assert len(loads) == 1, (way, loads)
    assert ["play", "http://127.0.0.1:41000/live.ts?t=x"] in k["live_buffer"]["ops"]
    assert ["set", "start", "600.0"] in k["movie_resume"]["ops"]
    assert ["set", "aid", 2] in k["tracks"]["ops"] and ["set", "sid", 3] in k["tracks"]["ops"]
    assert any(o[0] == "command" and o[1] == "loadfile" for o in k["web"]["ops"])
    # RTSP's transport options are a SAT>IP source's alone (core/rtsp.py).
    assert ["play", "rtsp://192.168.1.5:554/?src=1&freq=11494"] in k["rtsp"]["ops"]
    assert ["set", "mute", True] in k["muted"]["ops"]
    for way in ("no_stream", "no_core"):
        assert not k[way]["ok"] and k[way]["ops"] == [], way
    u = k["unchecked"]
    assert not u["ok"] and u["refusal"] and ["command", "stop"] in u["ops"]
    assert not any(o[0] == "play" for o in u["ops"])
