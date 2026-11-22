"""What ffmpeg and ffprobe may read for an address the app hands them: a
stream's protocols - never this computer's files by another name (concat:,
subfile:), a socket or a descriptor (unix:, fd:, pipe:), nor gopher:, which
sends what the address says to any port. A playlist's line is whatever its
author wrote; a file's own path keeps ffmpeg's own rule for files.

The last tests run the real ffmpeg, where there is one: a recording over
http and of an HLS list still records with the list in force.
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import threading

import pytest

from dopeiptv.core.player_exec import FFMPEG_PROTOCOLS
from dopeiptv.providers import airplay_bridge as ab
from dopeiptv.providers import cast_bridge as cb
from dopeiptv.providers import cast_feed

URL = "http://h.invalid/live/u/p/1.ts"
FILE = "/films/a film.mkv"


def _listed(cmd: list[str], source: str) -> bool:
    """Whether *cmd* names the list as an input option of *source*."""
    i = len(cmd) - 1 - cmd[::-1].index(source)
    assert cmd[i - 1] == "-i", cmd
    if "-protocol_whitelist" not in cmd:
        return False
    j = cmd.index("-protocol_whitelist")
    assert j < i and cmd[j + 1] == FFMPEG_PROTOCOLS
    return True


def test_ffprobe_takes_the_address_as_its_input_never_as_an_option():
    cmd = cb.probe_args("/usr/bin/ffprobe", "--version")
    assert cmd[-2:] == ["-i", "--version"]
    assert _listed(cb.probe_args("/usr/bin/ffprobe", URL), URL)
    assert not _listed(cb.probe_args("/usr/bin/ffprobe", FILE), FILE)


def test_a_casts_converter_reads_a_stream_with_a_streams_protocols(monkeypatch):
    monkeypatch.setattr(cb, "_hw_encoder", "libx264")
    assert _listed(cb.ffmpeg_args("ffmpeg", URL, True), URL)
    assert _listed(cb.hls_args("ffmpeg", URL, True, "/tmp/x", subs=0), URL)
    assert not _listed(cb.ffmpeg_args("ffmpeg", FILE, True), FILE)
    assert _listed(cast_feed.reader_args("ffmpeg", URL), URL)
    assert _listed(cast_feed.reader_args("ffmpeg", "http://127.0.0.1:5/t/s/1.ts"),
                   "http://127.0.0.1:5/t/s/1.ts")       # the rewind buffer's own server


def test_airplays_converter_the_same(monkeypatch):
    monkeypatch.setattr(cb, "_hw_encoder", "libx264")
    args = ab.ffmpeg_args("ffmpeg", ab.Plan(False, URL, live=True))
    assert _listed(args, URL)
    path = os.path.abspath(FILE)
    assert not _listed(ab.ffmpeg_args("ffmpeg", ab.Plan(False, FILE)), path)
    seen = {}

    def fake_run(cmd, **_kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1, b"", b"")

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    ab.probe(URL, exe="ffmpeg")
    assert _listed(seen["cmd"], URL)


def test_server_modes_hls_copy_the_same(monkeypatch):
    from dopeiptv.core import restream

    seen = {}

    class FakeProc:
        stdout = None

        def __init__(self, cmd, **_kw):
            seen["cmd"] = cmd

        def poll(self):
            return 0

    with monkeypatch.context() as m:
        m.setattr(subprocess, "Popen", FakeProc)
        restream.ffmpeg_opener("ffmpeg", None)("https://h.invalid/x/index.m3u8")
    assert _listed(seen["cmd"], "https://h.invalid/x/index.m3u8")


# -- the real ffmpeg ------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_a):
        pass


def _clean_env() -> dict[str, str]:
    """No proxy of the desktop's: the test's server is this computer."""
    return {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}


@pytest.fixture
def served(tmp_path):
    """A two-second MPEG-TS and an HLS list of it, served on loopback."""
    ff = shutil.which("ffmpeg") or "ffmpeg"
    root = tmp_path / "www"
    (root / "hls").mkdir(parents=True)
    made = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
         "testsrc=duration=2:size=160x120:rate=25", "-f", "lavfi", "-i", "sine=duration=2",
         "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "mpegts", str(root / "sample.ts")],
        capture_output=True, timeout=120, check=False)
    if made.returncode != 0 or not (root / "sample.ts").stat().st_size:
        pytest.skip("this ffmpeg cannot make the sample")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i",
                    str(root / "sample.ts"), "-c", "copy", "-f", "hls", "-hls_time", "1",
                    "-hls_list_size", "0", str(root / "hls" / "index.m3u8")],
                   capture_output=True, timeout=120, check=True)
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", root
    finally:
        srv.shutdown()
        srv.server_close()


def _record_command(tmp_path, monkeypatch, url) -> list[str]:
    """The recorder's own command for *url*, with the real ffmpeg."""
    from PyQt6.QtCore import QSettings

    from dopeiptv.core import recording
    from dopeiptv.core.recording import RecordingManager

    s = QSettings("dopeIPTV-test", "recreal")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    rm = RecordingManager(s)
    seen: list[list[str]] = []

    class FakeProc:
        def __init__(self, cmd, **_kw):
            seen.append(cmd)

        def poll(self):
            return None

    with monkeypatch.context() as m:        # the real Popen back for the run itself
        m.setattr(recording.subprocess, "Popen", FakeProc)
        m.setattr(RecordingManager, "recorder",
                  staticmethod(lambda: ("ffmpeg", shutil.which("ffmpeg"))))
        # The recorder reading the address itself - an HLS playlist's way,
        # and the way back for what the app cannot read (Feed.classic); a
        # stream the app reads and pipes over is tests/test_recording_feed.py's.
        rm._spawn({"url": url, "title": "T", "stop": None, "_classic": True})
    return seen[0]


@needs_ffmpeg
@pytest.mark.parametrize("what", ["sample.ts", "hls/index.m3u8"])
def test_the_real_ffmpeg_still_records_a_stream_and_an_hls_list(what, served, tmp_path,
                                                               monkeypatch):
    base, _root = served
    cmd = _record_command(tmp_path, monkeypatch, f"{base}/{what}")
    assert "-protocol_whitelist" in cmd
    done = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
                          env=_clean_env(), check=False)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-400:]
    assert os.path.getsize(cmd[-1]) > 10_000


@needs_ffmpeg
def test_the_real_ffmpeg_refuses_this_computers_files_by_another_name(served, tmp_path):
    """With the list a stream's address gets, ffmpeg itself turns away what
    a playlist behind that address could name."""
    base, root = served
    ff = shutil.which("ffmpeg") or "ffmpeg"
    sample = root / "sample.ts"
    for bad in (f"concat:{sample}|{sample}", f"subfile,,start,0,end,1000,,:{sample}",
                f"file://{sample}", f"cache:{base}/sample.ts", "gopher://127.0.0.1:9/_x"):
        out = tmp_path / "out.ts"
        done = subprocess.run(
            [ff, "-y", "-nostdin", "-loglevel", "error", "-protocol_whitelist",
             FFMPEG_PROTOCOLS, "-i", bad, "-c", "copy", str(out)],
            capture_output=True, timeout=60, env=_clean_env(), check=False)
        assert done.returncode != 0, bad
        assert not out.exists() or out.stat().st_size == 0, bad
