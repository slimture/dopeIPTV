"""Commands from outside: the command line (--play, --channel), dopeiptv://
links, and a second start handing its command to the copy already running
over a real local socket - checked end to end with the app's own main()."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from dopeiptv.core.commands import parse_command, parse_link
from dopeiptv.ui.mw_commands import find_channel

pytest.importorskip("PyQt6.QtNetwork")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- what a command line or a link asks --------------------------------------

@pytest.mark.parametrize("argv, want", [
    (["--play", "http://h/x.ts"], {"play": "http://h/x.ts"}),
    (["--play=rtsp://cam/1"], {"play": "rtsp://cam/1"}),
    (["--play", "/home/u/film.mkv"], {"play": "/home/u/film.mkv"}),
    (["--channel", "SVT1 HD"], {"channel": "SVT1 HD"}),
    (["--channel=TV4"], {"channel": "TV4"}),
    (["https://h/live.m3u8"], {"play": "https://h/live.m3u8"}),
    (["udp://@239.0.0.1:1234"], {"play": "udp://@239.0.0.1:1234"}),
    (["dopeiptv://open"], {"open": True}),
    (["--no-x11", "dopeiptv://channel/SVT1%20HD"], {"channel": "SVT1 HD"}),
    # Nothing to do: the options the app already reads, a bare word, a
    # --play of something that is no address, macOS's -psn_ argument.
    ([], None),
    (["--self-check"], None),
    (["--updated"], None),
    (["hello"], None),
    (["--play", "javascript:alert(1)"], None),
    (["--channel", "  "], None),
    (["-psn_0_12345"], None),
])
def test_a_command_line(argv, want):
    assert parse_command(argv) == want


@pytest.mark.parametrize("link, want", [
    ("dopeiptv://play?url=http%3A%2F%2Fh%2Fx.ts", {"play": "http://h/x.ts"}),
    ("dopeiptv://channel?name=TV4%20HD", {"channel": "TV4 HD"}),
    ("dopeiptv://channel/Kanal%205", {"channel": "Kanal 5"}),
    ("DOPEIPTV://OPEN", {"open": True}),
    ("dopeiptv://", {"open": True}),
    ("dopeiptv://play?url=file%3A%2F%2F%2Fetc%2Fpasswd", {"play": "file:///etc/passwd"}),
    # A link may not run anything but a stream: no other scheme, no
    # unknown verb, no play without an address.
    ("dopeiptv://play?url=javascript%3Aalert(1)", None),
    ("dopeiptv://play", None),
    ("dopeiptv://rm?path=/", None),
    ("http://h/x.ts", None),
    ("", None),
])
def test_a_link(link, want):
    assert parse_link(link) == want


def test_a_channel_by_name():
    rows = [{"stream_id": 1, "name": "SVT1 HD"}, {"stream_id": 2, "name": "SVT2"},
            {"stream_id": 3, "name": "TV4 Sport"}, {"stream_id": 4, "name": "TV4"},
            {"name": "TV4 — header row"}]
    assert find_channel(rows, "tv4")["stream_id"] == 4           # exact beats a start
    assert find_channel(rows, "svt1hd")["stream_id"] == 1        # letters and digits
    assert find_channel(rows, "SVT 2")["stream_id"] == 2
    assert find_channel(rows, "sport")["stream_id"] == 3         # held inside
    assert find_channel(rows, "Kanal 5") is None
    assert find_channel(rows, "") is None


# -- a second start hands over -----------------------------------------------

@pytest.fixture
def socket_name(monkeypatch):
    # A Unix socket path has a short limit: a folder of its own, short.
    d = tempfile.mkdtemp(prefix="dpi")
    name = os.path.join(d, "s")
    monkeypatch.setenv("DOPEIPTV_INSTANCE_SOCKET", name)
    yield name
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _wait_for(got, proc, seconds=20.0):
    from PyQt6.QtWidgets import QApplication
    end = time.monotonic() + seconds
    while time.monotonic() < end and (not got or proc.poll() is None):
        QApplication.processEvents()
        time.sleep(0.01)


def test_the_running_copy_gets_the_command(app, socket_name):
    from dopeiptv.core.instance import CommandServer, server_name
    assert server_name() == socket_name
    got: list[dict] = []
    server = CommandServer(got.append)
    assert server.listening
    sender = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, json\n"
         "from PyQt6.QtCore import QCoreApplication\n"
         "app = QCoreApplication([])\n"
         "from dopeiptv.core.instance import send_to_running\n"
         "sys.exit(0 if send_to_running(json.loads(sys.argv[1])) else 3)",
         json.dumps({"channel": "SVT1 HD"})],
        cwd=_REPO_ROOT, env=dict(os.environ))
    _wait_for(got, sender)
    assert sender.wait(10) == 0
    assert got == [{"channel": "SVT1 HD"}]
    server.close()


def test_nothing_running_means_no_hand_over(app, socket_name):
    from dopeiptv.core.instance import send_to_running
    assert send_to_running({"open": True}, timeout_ms=200) is False


def test_a_socket_left_by_a_crash_is_taken_over(app, socket_name):
    """A copy that crashed leaves its socket file behind; the next one
    listens anyway rather than never answering."""
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(socket_name)
    s.close()                                     # the file stays, nobody listens
    assert os.path.exists(socket_name)
    from dopeiptv.core.instance import CommandServer
    server = CommandServer(lambda _c: None)
    assert server.listening
    server.close()


def test_main_hands_over_and_opens_no_window(app, socket_name):
    """The app's own main(), started with --play while a copy runs, sends
    the command and ends - before any window, login or source."""
    from dopeiptv.core.instance import CommandServer
    got: list[dict] = []
    server = CommandServer(got.append)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    second = subprocess.Popen(
        [sys.executable, "-c",
         "import sys\n"
         "sys.argv = ['dopeiptv', '--play', 'http://10.0.0.5:8001/1:0:19:1B1D:802:2:11A0000:0:0:0:']\n"
         "from dopeiptv.app import main\n"
         "sys.exit(main())"],
        cwd=_REPO_ROOT, env=env)
    try:
        _wait_for(got, second, 60)
        assert second.wait(30) == 0
    finally:
        if second.poll() is None:     # it opened a window after all
            second.kill()
    assert got == [{"play": "http://10.0.0.5:8001/1:0:19:1B1D:802:2:11A0000:0:0:0:"}]
    server.close()


# -- the window does what it is asked -----------------------------------------

_CHILD = r"""
import os, sys, json, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
w = MainWindow(DemoClient(), s, PlaylistStore(s))
calls = []
w._start_playback = lambda url, title, icon, key, kind, record=True, item=None, catchup=False: \
    calls.append(["stream", url, title, kind, record])
w._play_local_path = lambda path: calls.append(["file", path])
w.play_live_channel = lambda it: calls.append(["channel", it["name"]])
w._show_toast = lambda text, ms=0: calls.append(["toast", text])
w._come_forward = lambda: calls.append(["forward"])

film = os.path.join(tempfile.mkdtemp(), "Film.mkv")
open(film, "wb").close()
for cmd in ({"play": "http://h/live/ch.ts?x=1"}, {"play": film},
            {"play": "file://" + film.replace(" ", "%20")},
            {"channel": "sintel"}, {"channel": "No such channel"}, {"open": True}):
    w.handle_command(cmd)
    for _ in range(200):
        app.processEvents()
        w.pool.waitForDone(10)
print("CALLS" + json.dumps(calls))
"""


def test_the_window_carries_out_each_command():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("CALLS")), None)
    assert line, out.stdout + out.stderr
    calls = json.loads(line[5:])
    streams = [c for c in calls if c[0] == "stream"]
    files = [c for c in calls if c[0] == "file"]
    # An address plays as a stream named by its last part, kept out of
    # Recently viewed; a file as one opened from Local files.
    assert streams == [["stream", "http://h/live/ch.ts?x=1", "ch.ts", "live", False]]
    assert len(files) == 2 and files[0][1] == files[1][1] and files[0][1].endswith("Film.mkv")
    assert ["channel", "Sintel"] in calls
    assert any(c[0] == "toast" and "No such channel" in c[1] for c in calls)
    assert calls.count(["forward"]) == 6
