"""An address is never an option of the player it is handed to.

A playlist's line is whatever its author wrote. One that starts with a dash
went to an external mpv or VLC as an option of theirs: "--log-file=..."
overwrote any file of the person's with mpv's log (the title, also the
playlist's, written into it), "--include=http://..." took mpv's whole
configuration from another server. The address goes after "--" now.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from dopeiptv.media import players

EVIL = ("--log-file=/tmp/x", "--include=http://evil.invalid/mpv.conf", "-v",
        "--script=/tmp/x.lua", "--sout=#std{access=file,dst=/tmp/x}")


@pytest.mark.parametrize("player", ["mpv", "vlc"])
@pytest.mark.parametrize("url", [*EVIL, "http://h.invalid/live/1.ts", "/films/a.mkv"])
def test_the_address_comes_last_after_the_end_of_the_options(player, url):
    cmd = players.player_command(player, "/usr/bin/" + player, url, "A title")
    assert cmd[0] == "/usr/bin/" + player
    assert cmd[-2:] == ["--", url]
    # Nothing of the address before the "--".
    assert url not in cmd[:-2] and cmd.count("--") == 1


@pytest.mark.parametrize("player", ["mpv", "vlc"])
def test_a_title_that_looks_like_an_option_stays_a_title(player):
    cmd = players.player_command(player, "/usr/bin/" + player, "http://h.invalid/1.ts",
                                 "--log-file=/tmp/x")
    head = cmd[1:cmd.index("--")]
    if player == "mpv":
        assert head[0] == "--force-media-title=--log-file=/tmp/x"
    else:
        # VLC takes the word after --meta-title as its value, whatever it is.
        assert head[:2] == ["--meta-title", "--log-file=/tmp/x"]


def test_launch_player_starts_that_command(monkeypatch):
    seen = []
    monkeypatch.setattr(players, "find_player_executable", lambda _p: "/usr/bin/mpv")
    monkeypatch.setattr(players.subprocess, "Popen", lambda cmd, **_k: seen.append(cmd))
    for url in ("http://h.invalid/live/u/p/1.ts", "rtsp://box.invalid/?src=1",
                "udp://@239.0.0.1:1234", "/films/a film.mkv", "C:\\Films\\a.mkv",
                "ytdl://https://www.youtube.com/watch?v=x"):
        seen.clear()
        players.launch_player("mpv", url, "T")
        assert seen == [players.player_command("mpv", "/usr/bin/mpv", url, "T")], url


NOT_STREAMS = (*EVIL, "concat:/etc/passwd|/etc/hosts", "subfile,,start,0,end,9,,:/etc/passwd",
               "gopher://127.0.0.1:6379/_FLUSHALL", "unix:///run/user/1000/bus", "fd:0",
               "pipe:0", "cache:http://h.invalid/1.ts", "memory://#EXTM3U", "edl://x",
               "av://v4l2:/dev/video0", "ytdl://file:///etc/passwd", "", " ")


@pytest.mark.parametrize("player", ["mpv", "vlc"])
@pytest.mark.parametrize("url", NOT_STREAMS)
def test_what_is_no_streams_address_never_reaches_a_player(player, url, monkeypatch):
    """Not a stream's address and not a file's path: no player is started
    for it at all, and the person is told (the words Open address has)."""
    started, told = [], []
    monkeypatch.setattr(players, "find_player_executable", lambda _p: "/usr/bin/" + player)
    monkeypatch.setattr(players.subprocess, "Popen", lambda cmd, **_k: started.append(cmd))
    monkeypatch.setattr(players.QMessageBox, "warning",
                        staticmethod(lambda *a, **_k: told.append(a[-1])))
    players.launch_player(player, url, "T")
    assert started == [] and len(told) == 1


def test_the_schemes_a_stream_has_and_what_ffmpeg_makes_of_an_address():
    from dopeiptv.core import player_exec as pe

    for url, scheme in (("http://h/1.ts", "http"), ("HTTPS://h/1.m3u8", "https"),
                        ("/films/a:b.mkv", ""), ("C:\\Films\\a.mkv", ""), ("c:/x.ts", ""),
                        ("film.mkv", ""), ("file:///films/a.mkv", "file"),
                        ("concat:/a|/b", "concat"), ("subfile,,start,0,end,9,,:/a", "subfile"),
                        ("crypto+file:///a", "crypto+file"), ("gopher://h:70/_x", "gopher"),
                        ("--log-file=/x", ""), ("", "")):
        assert pe.address_scheme(url) == scheme, url
    for url in ("http://h/1.ts", "https://h/x.m3u8", "rtsp://h/x", "rtmp://h/app/x",
                "rtp://239.0.0.1:5000", "udp://@239.0.0.1:1234", "srt://h:9000",
                "mms://h/x", "mmsh://h/x", "/films/a.mkv", "C:\\a.mkv", "file:///films/a.mkv"):
        assert pe.fetchable(url), url
    # A stream's address keeps ffmpeg to a stream's protocols; a file's own
    # path keeps ffmpeg's own rule for files.
    opts = pe.ffmpeg_protocols("http://h/x.m3u8")
    assert opts[0] == "-protocol_whitelist"
    listed = opts[1].split(",")
    for needed in ("http", "https", "tcp", "tls", "crypto", "udp", "rtp", "rtmp", "httpproxy"):
        assert needed in listed
    for never in ("file", "concat", "subfile", "pipe", "fd", "unix", "gopher", "cache", "async"):
        assert never not in listed
    assert pe.ffmpeg_protocols("/films/a.mkv") == pe.ffmpeg_protocols("file:///a.mkv") == []


def _recorder(tmp_path, monkeypatch, kind, name):
    from PyQt6.QtCore import QSettings

    from dopeiptv.core import recording
    from dopeiptv.core.recording import RecordingManager

    s = QSettings("dopeIPTV-test", name)
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    rm = RecordingManager(s)
    seen: list[list[str]] = []

    class FakeProc:
        def __init__(self, cmd, **_kw):
            seen.append(cmd)

        def poll(self):
            return None

    monkeypatch.setattr(recording.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(RecordingManager, "recorder",
                        staticmethod(lambda: (kind, "/usr/bin/" + kind)))
    return rm, seen


def test_the_mpv_recorder_takes_the_address_after_its_options(tmp_path, monkeypatch):
    """The recorder without ffmpeg is mpv: the address last, after "--"."""
    rm, seen = _recorder(tmp_path, monkeypatch, "mpv", "recdash")
    url = "http://h.invalid/live/u/p/1.ts"
    rm._spawn({"url": url, "title": "T", "stop": 9e12})
    cmd = seen[0]
    assert cmd[-2:] == ["--", url] and url not in cmd[:-2]
    assert any(a.startswith("--stream-record=") for a in cmd[:-2])
    assert any(a.startswith("--length=") for a in cmd[:-2])


@pytest.mark.parametrize("kind", ["ffmpeg", "mpv"])
@pytest.mark.parametrize("url", [u for u in NOT_STREAMS if u.strip()])
def test_what_is_no_streams_address_is_never_recorded(kind, url, tmp_path, monkeypatch):
    from dopeiptv.core.recording import NOT_A_STREAM

    rm, seen = _recorder(tmp_path, monkeypatch, kind, "recnostream")
    job = {"url": url, "title": "T", "stop": None, "status": "scheduled"}
    rm._spawn(job)
    assert seen == []
    assert job["status"] == "failed" and job["error"] == NOT_A_STREAM


def test_ffmpeg_records_a_stream_with_a_streams_protocols_only(tmp_path, monkeypatch):
    from dopeiptv.core.player_exec import FFMPEG_PROTOCOLS

    rm, seen = _recorder(tmp_path, monkeypatch, "ffmpeg", "recproto")
    for url in ("http://h.invalid/live/u/p/1.ts", "https://h.invalid/x.m3u8",
                "rtsp://box.invalid/?src=1", "udp://@239.0.0.1:1234"):
        seen.clear()
        rm._spawn({"url": url, "title": "T", "stop": None})
        cmd = seen[0]
        i = cmd.index("-i")
        assert cmd[i + 1] == url
        # An input option of that address, before its -i.
        assert cmd[cmd.index("-protocol_whitelist") + 1] == FFMPEG_PROTOCOLS
        assert cmd.index("-protocol_whitelist") < i
    # A file's own path: ffmpeg's own rule for files, nothing added.
    seen.clear()
    rm._spawn({"url": str(tmp_path / "a.ts"), "title": "T", "stop": None})
    assert "-protocol_whitelist" not in seen[0]


@pytest.mark.skipif(shutil.which("mpv") is None, reason="needs mpv")
def test_the_real_mpv_writes_no_file_an_address_names(tmp_path):
    """mpv itself, as it is installed here: the "address" that was a
    --log-file is now a file it cannot open, and nothing is written."""
    target = tmp_path / "clobbered.txt"
    target.write_text("the person's own file\n", encoding="utf-8")
    cmd = players.player_command("mpv", shutil.which("mpv") or "mpv",
                                 f"--log-file={target}", "T' $(touch pwned) '")
    cmd[1:1] = ["--no-config", "--vo=null", "--ao=null", "--no-terminal"]
    done = subprocess.run(cmd, cwd=tmp_path, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=60, check=False)
    assert done.returncode != 0                      # nothing by that name to play
    assert target.read_text(encoding="utf-8") == "the person's own file\n"
