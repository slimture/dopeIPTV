"""A recording over its own connection, read by the app and handed to the
recorder on its input (core/recording.Feed): the address never in the
recorder's command line, and the old way back for what the app cannot read."""
from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import threading
import time

import pytest

from dopeiptv.core import recording


def _ts_packets(n: int) -> bytes:
    pkt = bytes([0x47, 0x1F, 0xFF, 0x10]) + b"\xff" * 184      # null packets
    return pkt * n


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


def _serve(body: bytes, ctype: str, chunks: int = 1, pause: float = 0.0):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.server.paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            step = max(1, len(body) // chunks)
            for i in range(0, len(body), step):
                try:
                    self.wfile.write(body[i:i + step])
                    self.wfile.flush()
                except OSError:
                    return
                if pause:
                    time.sleep(pause)

        def log_message(self, *_a):
            pass

    srv = _Server(("127.0.0.1", 0), H)
    srv.paths = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _Proc:
    """A recorder that reads its input into memory."""

    def __init__(self):
        r, w = os.pipe()
        self.stdin = os.fdopen(w, "wb", buffering=0)
        self._r = os.fdopen(r, "rb")
        self.got = bytearray()
        self._t = threading.Thread(target=self._read, daemon=True)
        self._t.start()

    def _read(self):
        while True:
            b = self._r.read(65536)
            if not b:
                return
            self.got += b

    def done(self):
        self._t.join(5)
        return bytes(self.got)


def _open_plain(url, **kw):
    from dopeiptv.core.dvr_ingest import open_for
    return open_for(None, url, **kw)


def test_the_feed_hands_the_stream_over_on_the_recorders_input():
    body = _ts_packets(4000)
    srv = _serve(body, "video/mp2t", chunks=8)
    try:
        url = f"http://127.0.0.1:{srv.server_port}/live/user/secretpass/1.ts"
        proc = _Proc()
        feed = recording.Feed(proc, url, None, live=False, opener=_open_plain)
        feed.start()
        feed._thread.join(15)
        assert not feed.alive
        assert proc.done() == body
        assert feed.classic == ""
        assert srv.paths == ["/live/user/secretpass/1.ts"]
    finally:
        srv.shutdown()


def test_an_hls_playlist_goes_back_to_the_old_way():
    srv = _serve(b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n" + b"#EXTINF:6,\nseg1.ts\n" * 40,
                 "application/vnd.apple.mpegurl")
    try:
        url = f"http://127.0.0.1:{srv.server_port}/live/u/p/1.ts"
        proc = _Proc()
        feed = recording.Feed(proc, url, None, live=True, opener=_open_plain)
        feed.start()
        feed._thread.join(15)
        assert feed.classic == "an HLS playlist"
        assert proc.done() == b""
    finally:
        srv.shutdown()


def test_a_certificate_the_app_cannot_check_falls_back_only_without_the_strict_check():
    from requests.exceptions import SSLError

    def refusing(url, **_kw):
        raise SSLError("certificate verify failed")

    for strict, expected in ((False, "a certificate the app cannot check"), (True, "")):
        proc = _Proc()
        feed = recording.Feed(proc, "https://h.example/live/u/p/1.ts", None,
                              live=False, opener=refusing, strict=strict)
        feed.start()
        feed._thread.join(15)
        assert feed.classic == expected
        proc.done()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg here")
def test_ffmpeg_records_what_the_app_reads_for_it(tmp_path):
    src = tmp_path / "src.ts"
    made = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=duration=3:size=160x120:rate=25", "-c:v", "mpeg2video",
         "-f", "mpegts", str(src)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("this ffmpeg cannot make the test stream")
    body = src.read_bytes()
    srv = _serve(body, "video/mp2t", chunks=6, pause=0.05)
    try:
        from PyQt6.QtCore import QSettings
        s = QSettings("dopeIPTV-test", "recfeed")
        s.clear()
        s.setValue("recordings_dir", str(tmp_path / "rec"))
        rm = recording.RecordingManager(s)
        rm.recorder = staticmethod(lambda: ("ffmpeg", shutil.which("ffmpeg")))  # type: ignore[method-assign]
        url = f"http://127.0.0.1:{srv.server_port}/timeshift/user/secretpass/60/x/1.ts"
        j = {"id": "a", "url": url, "title": "Feed test", "stop": None,
             "status": "scheduled", "start": time.time()}
        rm._spawn(j)
        assert j["status"] == "recording", j.get("error")
        try:
            with open(f"/proc/{j['proc'].pid}/cmdline", "rb") as fh:
                assert b"secretpass" not in fh.read()
        except FileNotFoundError:
            pass                                        # not Linux
        assert j["proc"].wait(30) == 0
        assert os.path.getsize(j["path"]) > 0
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "stream=codec_name", "-of", "csv=p=0", j["path"]],
                               capture_output=True, text=True)
        assert "mpeg2video" in probe.stdout
    finally:
        srv.shutdown()


def test_the_tick_starts_the_old_way_once_when_the_feed_says_so(tmp_path, monkeypatch):
    """An HLS playlist after all: the next tick starts the recorder on the
    address itself, once - not a failed recording, and never in a loop."""
    from PyQt6.QtCore import QSettings
    s = QSettings("dopeIPTV-test", "recfeedtick")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    rm = recording.RecordingManager(s)
    seen = []

    class FakeProc:
        stdin = None

        def __init__(self, cmd, **_kw):
            seen.append(cmd)

        def poll(self):
            return 1

    class FakeFeed:
        alive = False
        classic = "an HLS playlist"

        def __init__(self, *_a, **_k):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(recording.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(recording, "Feed", FakeFeed)
    monkeypatch.setattr(recording.RecordingManager, "recorder",
                        staticmethod(lambda: ("ffmpeg", "/usr/bin/ffmpeg")))
    from urllib.parse import urlsplit

    from dopeiptv.core import net_bridge
    url = "http://h.invalid/live/u/p/1.ts"
    j = {"id": "a", "url": url, "title": "T", "stop": None, "status": "scheduled",
         "start": time.time()}
    rm.jobs = [j]
    rm._spawn(j)
    assert seen[-1][seen[-1].index("-i") + 1] == "pipe:0"
    rm.tick()
    # The recorder reads it itself now - through the bridge, under an alias
    # that names nothing of the address.
    alias = seen[-1][seen[-1].index("-i") + 1]
    assert len(seen) == 2 and (urlsplit(alias).hostname or "").endswith(net_bridge.ALIAS_HOST)
    assert not any("/u/p/" in a or "h.invalid" in a for a in seen[-1])
    bridge = j["_alias"][0]
    assert j["_classic"] and j["status"] == "recording" and bridge.aliases == 1
    rm.tick()                                   # that ended too: failed, no third try
    assert len(seen) == 2 and j["status"] == "failed"
    assert "_alias" not in j and bridge.aliases == 0       # the alias forgotten with it
