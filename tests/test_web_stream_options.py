"""A web page through yt-dlp opens as mpv itself opens it: the IPTV tuning
(reconnect, short probe, buffer) is off for that file alone, on any libmpv."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from dopeiptv.media.embedded import WEB_FILE_OPTIONS, load_with_options

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Core:
    def __init__(self, takes: int | None):
        self.takes, self.calls, self.played = takes, [], []

    def command(self, *args):
        self.calls.append(args)
        if len(args) - 1 != self.takes:
            raise SystemError("mpv command returned error")

    def play(self, url):
        self.played.append(url)


def test_mpv_0_38_and_newer_take_the_index_form():
    core = Core(takes=4)
    assert load_with_options(core, "ytdl://x", {"a": "1", "b": ""})
    assert core.calls == [("loadfile", "ytdl://x", "replace", "-1", "a=1,b=")]
    assert core.played == []


def test_older_mpv_takes_the_three_argument_form():
    core = Core(takes=3)
    assert load_with_options(core, "ytdl://x", {"a": "1"})
    assert core.calls[-1] == ("loadfile", "ytdl://x", "replace", "a=1")


def test_a_libmpv_that_takes_neither_still_plays():
    core = Core(takes=None)
    assert not load_with_options(core, "ytdl://x", {"a": "1"})
    assert core.played == ["ytdl://x"]


def test_the_options_are_mpvs_own_defaults():
    assert WEB_FILE_OPTIONS["demuxer-lavf-o"] == ""
    assert WEB_FILE_OPTIONS["demuxer-lavf-analyzeduration"] == "0"
    # mpv refuses probesize 0 (32..INT_MAX): FFmpeg's own default instead.
    assert int(WEB_FILE_OPTIONS["demuxer-lavf-probesize"]) == 5_000_000


def test_only_the_web_file_has_them_on_a_real_mpv(tmp_path):
    try:
        import mpv
        core = mpv.MPV(vo="null", ao="null", terminal=False, idle=True,
                       keep_open="yes")
    except Exception as e:
        pytest.skip(f"libmpv not here: {e}")
    clip = tmp_path / "a.ts"
    try:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=160x120:rate=25", "-t", "5", "-c:v", "mpeg2video",
                        str(clip)], check=True, timeout=60)
    except Exception as e:
        core.terminate()
        pytest.skip(f"no ffmpeg to make a clip: {e}")
    try:
        core["demuxer-lavf-o"] = "reconnect=1,reconnect_streamed=1"
        core["demuxer-lavf-probesize"] = 2_000_000
        core["cache-secs"] = 10.0
        assert load_with_options(core, str(clip), WEB_FILE_OPTIONS)
        core.wait_until_playing(timeout=10)
        assert core["demuxer-lavf-o"] == {}
        assert core["demuxer-lavf-probesize"] == 5_000_000
        assert core["cache-secs"] == 3_600_000
        core.play(os.fspath(clip))
        core.wait_until_playing(timeout=10)
        core.wait_for_property("demuxer-lavf-probesize", lambda v: v == 2_000_000,
                               timeout=5)
        assert core["demuxer-lavf-o"] == {"reconnect": "1", "reconnect_streamed": "1"}
        assert core["cache-secs"] == 10.0
    finally:
        core.terminate()


def _core_and_clips(tmp_path, n):
    """A real core and *n* names of one test clip: which file plays is told
    by its name (wait_until_playing returns while the last file still plays)."""
    try:
        import mpv
        core = mpv.MPV(vo="null", ao="null", terminal=False, idle=True,
                       keep_open="yes")
    except Exception as e:
        pytest.skip(f"libmpv not here: {e}")
    clip = tmp_path / "a.ts"
    try:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=160x120:rate=25", "-t", "5", "-c:v", "mpeg2video",
                        str(clip)], check=True, timeout=60)
    except Exception as e:
        core.terminate()
        pytest.skip(f"no ffmpeg to make a clip: {e}")
    names = []
    for i in range(n):
        name = tmp_path / f"{i}.ts"
        shutil.copyfile(clip, name)
        names.append(os.fspath(name))
    return core, names


def _started(core, path, timeout=10.0):
    """*path* is the file playing, past its start (its options are in)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if core.path == path and core.time_pos is not None:
            return
        time.sleep(0.01)
    raise AssertionError(f"{path} did not start (path {core.path!r})")


def test_what_the_next_stream_sets_holds_once_the_web_file_has_ended(tmp_path):
    """mpv puts back what a file's own options replaced when that file ends -
    with loadfile replace, after the next stream wrote its own. Ended first
    (end_file_options), the next stream's hold."""
    from dopeiptv.media.embedded import end_file_options
    core, clips = _core_and_clips(tmp_path, 12)
    try:
        core["cache-secs"] = 10.0
        core["demuxer-lavf-probesize"] = 2_000_000
        # mpv's own behaviour, what the fix is for: written while the web
        # file is loaded, gone once the next file plays.
        assert load_with_options(core, clips[0], WEB_FILE_OPTIONS)
        _started(core, clips[0])
        core["cache-secs"] = 2.0
        core["demuxer-lavf-probesize"] = 500_000
        core.play(clips[1])
        _started(core, clips[1])
        assert core["cache-secs"] == 10.0 and core["demuxer-lavf-probesize"] == 2_000_000
        # Ended first: what is written then is what the next file plays with.
        for web, after in zip(clips[2::2], clips[3::2], strict=True):
            assert load_with_options(core, web, WEB_FILE_OPTIONS)
            _started(core, web)
            assert core["cache-secs"] == 3_600_000
            assert end_file_options(core)
            assert core.path is None and core["cache-secs"] == 10.0
            core["cache-secs"] = 2.0
            core["demuxer-lavf-probesize"] = 500_000
            core["demuxer-lavf-o"] = "fflags=+nobuffer"
            core.play(after)
            _started(core, after)
            assert core["cache-secs"] == 2.0
            assert core["demuxer-lavf-probesize"] == 500_000
            assert core["demuxer-lavf-o"] == {"fflags": "+nobuffer"}
            core["cache-secs"] = 10.0
            core["demuxer-lavf-probesize"] = 2_000_000
        # Nothing loaded (a web page that failed, or Stop): nothing to wait for.
        core.command("stop")
        core.wait_for_property("path", lambda v: v is None, timeout=5)
        assert end_file_options(core, wait=0.0)
    finally:
        core.terminate()


_PLAYER_CHILD = r"""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
import mpv
from dopeiptv.core import live_latency, nerd
from dopeiptv.media.embedded import EmbeddedPlayer

clips = iter(sys.argv[1:])
app = QApplication.instance() or QApplication([])
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
for k, v in {"nerd_enabled": "true", live_latency.KEY: "true", "live_rewind_minutes": 0,
             "cache_secs": 10}.items():
    s.setValue(k, v)
nerd.install(s)
p = EmbeddedPlayer(settings=s)
import locale
locale.setlocale(locale.LC_NUMERIC, "C")
m = p.video.mpv = mpv.MPV(vo="null", ao="null", terminal=False, idle=True, keep_open="yes")
m["demuxer-lavf-o"] = "reconnect=1,reconnect_streamed=1"
m["demuxer-lavf-probesize"] = 2_000_000


def play(title, **kw):
    path = next(clips)
    assert p.play(path, title, **kw)
    end = time.monotonic() + 10
    while not (m.path == path and m.time_pos is not None):
        assert time.monotonic() < end, (title, m.path)
        time.sleep(0.01)


def low_latency():
    return (m["cache-secs"] == 2.0 and m["demuxer-lavf-probesize"] == 500_000
            and m["demuxer-lavf-o"].get("fflags") == "+nobuffer")


# A live channel straight from the provider: low latency.
play("Live", local_buffer=True)
assert low_latency(), (m["cache-secs"], m["demuxer-lavf-probesize"], m["demuxer-lavf-o"])
for _round in range(3):
    # A web page: mpv's own options for that file.
    play("Web", web=True)
    assert m["cache-secs"] == 3_600_000 and m["demuxer-lavf-o"] == {}, m["cache-secs"]
    # The live channel after it has its low latency again - and keeps it.
    play("Live", local_buffer=True)
    assert low_latency(), (m["cache-secs"], m["demuxer-lavf-probesize"], m["demuxer-lavf-o"])
    time.sleep(0.3)
    assert low_latency(), (m["cache-secs"], m["demuxer-lavf-probesize"], m["demuxer-lavf-o"])
# A film after a web page: the buffer Settings asks for now, no low latency.
play("Web", web=True)
s.setValue("cache_secs", 30)
play("Film")
assert m["cache-secs"] == 30.0 and m["demuxer-lavf-probesize"] == 2_000_000, m["cache-secs"]
assert m["demuxer-lavf-o"].get("reconnect") == "1" and "fflags" not in m["demuxer-lavf-o"]
print("WEB_THEN_LIVE_OK", flush=True)
m.terminate()
os._exit(0)
"""


def test_the_player_gives_the_stream_after_a_web_page_its_own_options(tmp_path):
    """EmbeddedPlayer.play on a real core: a live channel after a web page
    plays with Nerd's low latency, a film with the buffer set meanwhile."""
    pytest.importorskip("PyQt6.QtWidgets")
    core, clips = _core_and_clips(tmp_path, 10)
    core.terminate()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _PLAYER_CHILD, *clips], cwd=_REPO_ROOT,
                         env=env, capture_output=True, text=True, timeout=180)
    assert "WEB_THEN_LIVE_OK" in out.stdout, out.stdout + out.stderr[-3000:]


def test_the_window_asks_for_it_only_for_a_web_page():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "dopeiptv/ui/main_window.py").read_text()
    assert 'web=kind == "web"' in src
