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
    (["--no-x11", "dopeiptv://channel/SVT1%20HD"], {"channel": "SVT1 HD", "link": True}),
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


def test_a_windows_path_on_the_command_line(monkeypatch):
    from dopeiptv.core import commands
    monkeypatch.setattr(commands.sys, "platform", "win32")
    assert parse_command(["--play", r"C:\Films\a.mkv"]) == {"play": r"C:\Films\a.mkv"}
    assert parse_command([r"--play=\\nas\films\a.mkv"]) == {"play": r"\\nas\films\a.mkv"}
    assert parse_command(["--play", r"Films\a.mkv"]) is None          # relative: no
    monkeypatch.setattr(commands.sys, "platform", "linux")
    assert parse_command(["--play", r"C:\Films\a.mkv"]) is None


@pytest.mark.parametrize("url, posix, windows", [
    ("file:///home/u/My%20Film.mkv", "/home/u/My Film.mkv", r"\home\u\My Film.mkv"),
    ("file:///C:/Films/My%20Film.mkv", "/C:/Films/My Film.mkv", r"C:\Films\My Film.mkv"),
    ("file://localhost/C:/Films/a.mkv", "/C:/Films/a.mkv", r"C:\Films\a.mkv"),
    ("file://C:/Films/a.mkv", "/Films/a.mkv", r"C:\Films\a.mkv"),
    ("file:///C|/Films/a.mkv", "/C|/Films/a.mkv", r"C:\Films\a.mkv"),
    ("file://nas/films/a.mkv", "/films/a.mkv", r"\\nas\films\a.mkv"),
])
def test_a_file_address_is_the_path_it_names(url, posix, windows):
    from dopeiptv.core.commands import file_url_path
    assert file_url_path(url, windows=False) == posix
    assert file_url_path(url, windows=True) == windows


@pytest.mark.parametrize("link, want", [
    # A link's address is marked as one: the window asks before it plays.
    ("dopeiptv://play?url=http%3A%2F%2Fh%2Fx.ts", {"play": "http://h/x.ts", "link": True}),
    ("dopeiptv://channel?name=TV4%20HD", {"channel": "TV4 HD", "link": True}),
    ("dopeiptv://channel/Kanal%205", {"channel": "Kanal 5", "link": True}),
    ("DOPEIPTV://OPEN", {"open": True}),
    ("dopeiptv://", {"open": True}),
    # Any web page can open a link: it plays a stream, never a file here.
    ("dopeiptv://play?url=file%3A%2F%2F%2Fetc%2Fpasswd", None),
    ("dopeiptv://play?url=FILE%3A%2F%2F%2Fhome%2Fu%2Fx.mkv", None),
    ("dopeiptv://play?url=%2Fhome%2Fu%2Ffilm.mkv", None),
    ("dopeiptv://play?url=C%3A%5CUsers%5Cu%5Cx.mkv", None),
    # The supporter key is gone: its old link does nothing.
    ("dopeiptv://supporter?key=DS1-AQALYXcAAAAAAAAACmF8F1Jg", None),
    ("dopeiptv://supporter", None),
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


@pytest.mark.skipif(not hasattr(__import__("socket"), "AF_UNIX"),
                    reason="the crashed copy's socket is a Unix socket")
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


@pytest.fixture
def runtime_dir(monkeypatch):
    """A user's runtime folder ($XDG_RUNTIME_DIR), short: a socket's path
    has a limit."""
    d = tempfile.mkdtemp(prefix="dpr")
    os.chmod(d, 0o700)
    for var in ("DOPEIPTV_INSTANCE_SOCKET", "FLATPAK_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_the_socket_is_named_in_the_users_own_folder_not_tmp(runtime_dir, monkeypatch):
    """/tmp is everyone's: another account could take the name there first
    and hear this one's commands. On Linux the user's runtime folder;
    Windows keeps its pipe; elsewhere (macOS, a BSD) a folder of the
    user's own in the temporary folder, never the folder itself."""
    from dopeiptv.core import instance
    monkeypatch.setattr(instance.sys, "platform", "linux")
    name = instance.server_name()
    assert os.path.dirname(name) == os.path.join(runtime_dir, "dopeiptv")
    assert os.path.basename(name).startswith("dopeiptv-")
    own_tmp = os.path.join(tempfile.gettempdir(), f"dopeiptv-{os.getuid()}") if os.name == "posix" else ""
    monkeypatch.setattr(instance.sys, "platform", "win32")
    assert "/" not in instance.server_name()
    if own_tmp:
        for other in ("darwin", "freebsd14"):
            monkeypatch.setattr(instance.sys, "platform", other)
            assert os.path.dirname(instance.server_name()) == own_tmp
    monkeypatch.setattr(instance.sys, "platform", "linux")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    cache = tempfile.mkdtemp(prefix="dpc")
    monkeypatch.setenv("XDG_CACHE_HOME", cache)
    # No runtime folder: a folder of the user's own in their cache, not /tmp.
    assert os.path.dirname(instance.server_name()) == os.path.join(cache, "dopeiptv", "instance")
    monkeypatch.setenv("XDG_CACHE_HOME", "/" + "x" * instance.SOCKET_PATH_MAX)
    if own_tmp:                                   # too long for a socket: the temporary folder's own
        assert os.path.dirname(instance.server_name()) == own_tmp
    shutil.rmtree(cache, ignore_errors=True)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux's socket folder")
def test_without_a_runtime_folder_a_second_start_reaches_the_copy_through_its_own(
        app, monkeypatch):
    from dopeiptv.core import instance
    for var in ("DOPEIPTV_INSTANCE_SOCKET", "FLATPAK_ID", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(var, raising=False)
    cache = tempfile.mkdtemp(prefix="dpc")
    monkeypatch.setenv("XDG_CACHE_HOME", cache)
    folder = os.path.join(cache, "dopeiptv", "instance")
    os.makedirs(folder)
    os.chmod(folder, 0o755)                     # made wider before: narrowed
    got: list[dict] = []
    server = instance.CommandServer(got.append)
    try:
        assert server.listening
        name = instance.server_name()
        assert name.startswith(folder) and os.path.exists(name)
        assert os.stat(folder).st_mode & 0o777 == 0o700
        env = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
        sender = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, json\n"
             "from PyQt6.QtCore import QCoreApplication\n"
             "app = QCoreApplication([])\n"
             "from dopeiptv.core.instance import send_to_running\n"
             "sys.exit(0 if send_to_running(json.loads(sys.argv[1])) else 3)",
             json.dumps({"open": True})],
            cwd=_REPO_ROOT, env=env)
        _wait_for(got, sender)
        assert sender.wait(10) == 0 and got == [{"open": True}]
    finally:
        server.close()
        shutil.rmtree(cache, ignore_errors=True)


@pytest.mark.skipif(os.name != "posix", reason="folder owners")
def test_a_socket_folder_another_account_owns_is_not_listened_in(app, socket_name, monkeypatch):
    from dopeiptv.core import instance
    monkeypatch.setattr(instance.os, "getuid", lambda: os.stat(os.path.dirname(socket_name)).st_uid + 1)
    server = instance.CommandServer(lambda _c: None)
    assert not server.listening and not os.path.exists(socket_name)
    server.close()


@pytest.mark.skipif(os.name != "posix", reason="folder owners and links")
def test_a_link_in_place_of_the_socket_folder_is_not_listened_in(app, monkeypatch, tmp_path):
    """In a temporary folder every account shares, another could leave a
    link where the socket's folder goes - to a folder of this user's own,
    which the old check (stat, following it) took for the right one."""
    from dopeiptv.core import instance
    target = tmp_path / "home-folder"
    target.mkdir()
    os.chmod(target, 0o755)
    link = tmp_path / "dpl"
    link.symlink_to(target)
    monkeypatch.setenv("DOPEIPTV_INSTANCE_SOCKET", str(link / "s"))
    server = instance.CommandServer(lambda _c: None)
    assert not server.listening and not (target / "s").exists()
    assert os.stat(target).st_mode & 0o777 == 0o755, "not narrowed through the link"
    server.close()


@pytest.mark.skipif(os.name != "posix", reason="folder modes")
def test_a_socket_folder_that_cannot_be_made_the_users_own_is_not_listened_in(
        app, monkeypatch, tmp_path, caplog):
    """It fails closed: a folder that could not be made, or narrowed to
    this user alone, is never listened in - the app starts without the
    second-start handoff, said once, as without Qt's network library."""
    import logging

    from dopeiptv.core import instance
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    monkeypatch.setenv("DOPEIPTV_INSTANCE_SOCKET", str(blocker / "s"))   # its folder is a file
    with caplog.at_level(logging.WARNING, logger="dopeiptv"):
        server = instance.CommandServer(lambda _c: None)
    assert not server.listening
    said = [r for r in caplog.records if "not listening" in r.getMessage()]
    assert len(said) == 1 and "a window of its own" in said[0].getMessage()
    server.close()

    wide = tmp_path / "wide"
    wide.mkdir()
    os.chmod(wide, 0o755)
    monkeypatch.setenv("DOPEIPTV_INSTANCE_SOCKET", str(wide / "s"))

    def refused(*_a, **_k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(instance.os, "chmod", refused)
    server = instance.CommandServer(lambda _c: None)
    assert not server.listening and not (wide / "s").exists()
    server.close()
    monkeypatch.setattr(instance.os, "chmod", lambda *_a, **_k: None)   # "done", and not so
    server = instance.CommandServer(lambda _c: None)
    assert not server.listening and not (wide / "s").exists()
    assert instance._folder_refusal(str(wide / "s"))
    server.close()


def test_a_windows_pipe_s_name_holds_a_word_only_its_user_can_read(monkeypatch, tmp_path):
    """Named pipes are the whole machine's: another account could make
    dopeiptv-<user> first and hear this one's commands. The name carries a
    random word kept in the user's own local app data."""
    from dopeiptv.core import instance
    monkeypatch.delenv("DOPEIPTV_INSTANCE_SOCKET", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(instance.sys, "platform", "win32")
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    monkeypatch.setenv("LOCALAPPDATA", str(mine))
    first = instance.server_name()
    base, key = first.rsplit("-", 1)
    assert base.startswith("dopeiptv-") and len(key) == 32 and "/" not in first
    assert instance.server_name() == first, "every start of this user: the same name"
    assert (mine / "dopeiptv" / instance.PIPE_KEY).read_text() == key
    monkeypatch.setenv("LOCALAPPDATA", str(theirs))
    assert instance.server_name() != first, "another account's word is its own"
    (theirs / "dopeiptv" / instance.PIPE_KEY).write_text("not a key")
    assert instance.server_name() == base, "a damaged key: the name as before"
    monkeypatch.delenv("LOCALAPPDATA")
    assert instance.server_name() == base


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the runtime folder is Linux's")
def test_a_second_start_reaches_the_copy_through_the_runtime_folder(app, runtime_dir):
    from dopeiptv.core.instance import CommandServer, server_name
    got: list[dict] = []
    server = CommandServer(got.append)
    try:
        assert server.listening
        name = server_name()
        assert os.path.exists(name) and name.startswith(runtime_dir)
        assert os.stat(os.path.dirname(name)).st_mode & 0o777 == 0o700
        assert os.stat(name).st_mode & 0o077 == 0, oct(os.stat(name).st_mode)   # the user's alone
        sender = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, json\n"
             "from PyQt6.QtCore import QCoreApplication\n"
             "app = QCoreApplication([])\n"
             "from dopeiptv.core.instance import send_to_running\n"
             "sys.exit(0 if send_to_running(json.loads(sys.argv[1])) else 3)",
             json.dumps({"open": True})],
            cwd=_REPO_ROOT, env=dict(os.environ))
        _wait_for(got, sender)
        assert sender.wait(10) == 0 and got == [{"open": True}]
    finally:
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
w._start_playback = lambda url, title, icon, key, kind, record=True, item=None, catchup=False, \
    own_buffer=True: calls.append(["stream", url, title, kind, record, own_buffer])
w._play_local_path = lambda path: calls.append(["file", path])
w.play_live_channel = lambda it: calls.append(["channel", it["name"]])
w._show_toast = lambda text, ms=0: calls.append(["toast", text])
w._come_forward = lambda: calls.append(["forward"])

film = os.path.join(tempfile.mkdtemp(), "Film.mkv")
open(film, "wb").close()
from pathlib import Path
# The file's address as the system makes one: file:///C:/... on Windows.
for cmd in ({"play": "http://h/live/ch.ts?x=1"}, {"play": film},
            {"play": Path(film).as_uri()},
            {"channel": "sintel"}, {"channel": "No such channel"}, {"open": True}):
    w.handle_command(cmd)
    for _ in range(200):
        app.processEvents()
        w.pool.waitForDone(10)
print("CALLS" + json.dumps(calls), flush=True)
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
    # Recently viewed and out of dopeIPTV's own rewind buffer (mpv opens
    # it: it need not be a stream); a file as one opened from Local files.
    assert streams == [["stream", "http://h/live/ch.ts?x=1", "ch.ts", "live", False, False]]
    assert len(files) == 2 and files[0][1] == files[1][1] and files[0][1].endswith("Film.mkv")
    assert ["channel", "Sintel"] in calls
    assert any(c[0] == "toast" and "No such channel" in c[1] for c in calls)
    assert calls.count(["forward"]) == 6


def test_an_old_supporter_command_is_only_a_come_forward():
    """The supporter key is gone: a command that still names one opens
    nothing and takes nothing."""
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_commands import _CommandsMixin

    class Host(_CommandsMixin):
        def __init__(self):
            self.forward = 0

        def _come_forward(self):
            self.forward += 1
    h = Host()
    h.handle_command({"supporter": "DS1-abcdefghijklmnopqrstuv"})
    assert h.forward == 0 and not hasattr(h, "open_supporter")
