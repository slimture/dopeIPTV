"""An HLS film (an .m3u8) is downloaded by ffmpeg through the app's bridge,
under an alias, exactly as server mode copies an HLS channel - its command
line names nothing of the address, the login or the token - and saved as
MP4 the way a recording is (core/rec_mp4.remux). The real ffmpeg, a
provider of our own on this computer."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import downloads as dl
from dopeiptv.core import nerd, net_bridge, privacy

INTERNET_SRC = {"_kind": "m3u", "_server": "http://provider.example/list.m3u"}


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.seen: list[str] = []
        super().__init__(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    @property
    def port(self) -> int:
        return self.server_address[1]


class _Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        self.server.seen.append(self.path)
        got = self.server.routes.get(self.path)
        if got is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        ctype, body = got
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def hls(tmp_path):
    ff = shutil.which("ffmpeg")
    if ff is None:
        pytest.skip("needs ffmpeg")
    work = tmp_path / "make"
    work.mkdir()
    sample = work / "sample.ts"
    for video, audio in (("libx264", "aac"), ("mpeg2video", "mp2")):
        made = subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
             "testsrc=duration=3:size=160x120:rate=25", "-f", "lavfi", "-i", "sine=duration=3",
             "-c:v", video, "-g", "25", "-c:a", audio, "-f", "mpegts", str(sample)],
            capture_output=True, timeout=120, check=False)
        if made.returncode == 0:
            break
    else:
        pytest.skip("this ffmpeg cannot make the sample")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(sample),
                    "-c", "copy", "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
                    "-hls_segment_filename", str(work / "seg%d.ts"), str(work / "index.m3u8")],
                   capture_output=True, timeout=120, check=True)
    routes = {"/movie/alice/s3cr3tpass/index.m3u8?token=t0k3n":
              ("application/vnd.apple.mpegurl", (work / "index.m3u8").read_bytes())}
    for seg in sorted(work.glob("seg*.ts")):
        routes[f"/movie/alice/s3cr3tpass/{seg.name}"] = ("video/mp2t", seg.read_bytes())
    srv = Server(routes)
    try:
        yield srv, ff
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY",
              "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(k, raising=False)
    kept = type(net_bridge._bridges)()
    monkeypatch.setattr(net_bridge, "_bridges", kept)
    nerd.install(None)
    privacy.set_private(False)
    yield kept
    nerd.install(None)
    for b in list(kept.values()):
        b.close()


def test_an_hls_film_through_the_bridge_into_an_mp4(hls, tmp_path, monkeypatch):
    srv, ff = hls
    url = f"http://127.0.0.1:{srv.port}/movie/alice/s3cr3tpass/index.m3u8?token=t0k3n"
    argv: list[list[str]] = []
    real = subprocess.Popen

    def spy(args, **kw):
        argv.append([str(a) for a in args])
        return real(args, **kw)
    monkeypatch.setattr(subprocess, "Popen", spy)
    s = Settings({dl.DIR_KEY: str(tmp_path / "Downloads")})
    m = dl.Downloads(s, resolve=lambda _j: (url, dict(INTERNET_SRC)), ffmpeg=lambda: ff,
                     gated=False, free=lambda _p: 10 ** 13)
    m.start()
    try:
        job = m.add("movie", "a1b2c3d4", "77", title="Testbild", meta={"year": "2024"})
        end = time.monotonic() + 90
        while time.monotonic() < end and m.find("a1b2c3d4", "movie", "77") is None:
            j = m.job(job.id)
            if j is not None and j.state == dl.FAILED:
                break
            time.sleep(0.05)
        item = m.find("a1b2c3d4", "movie", "77")
        assert item is not None, m.jobs()
    finally:
        m.close()
    assert item.path.endswith(os.path.join("Movies", "Testbild (2024).mp4"))
    with open(item.path, "rb") as fh:
        assert fh.read(12)[4:8] == b"ftyp"                  # an MP4, made as a recording's
    assert not [f for f in os.listdir(os.path.dirname(item.path)) if f.endswith((".part", ".ts"))]
    reader = next(a for a in argv if "pipe:1" in a)
    words = " ".join(reader)
    for word in ("alice", "s3cr3tpass", "t0k3n", str(srv.port), "127.0.0.1", "provider.example"):
        assert word not in words, word
    assert ".stream.invalid/" in words                        # an alias through the bridge
    i = reader.index("-protocol_whitelist")
    assert reader[i + 1] == "http,https,tcp,tls,httpproxy,crypto,data"
    assert any(p.endswith("index.m3u8?token=t0k3n") for p in srv.seen)
    assert sum(p.endswith(".ts") for p in srv.seen) >= 3
    text = open(item.path + dl.SIDECAR, encoding="utf-8").read()
    assert "alice" not in text and "s3cr3tpass" not in text and "://" not in text
