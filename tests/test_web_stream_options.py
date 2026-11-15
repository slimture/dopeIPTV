"""A web page through yt-dlp opens as mpv itself opens it: the IPTV tuning
(reconnect, short probe, buffer) is off for that file alone, on any libmpv."""
from __future__ import annotations

import os
import subprocess

import pytest

from dopeiptv.media.embedded import WEB_FILE_OPTIONS, load_with_options


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


def test_the_window_asks_for_it_only_for_a_web_page():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "dopeiptv/ui/main_window.py").read_text()
    assert 'web=kind == "web"' in src
