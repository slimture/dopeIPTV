"""Discord status over Discord's local IPC: the frames, where Discord
listens, what the status says, and a stand-in Discord on a Unix socket that
the presence thread finds, loses and finds again - never holding up the
caller."""
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

import pytest

from dopeiptv.core import discord_rpc as rpc

unix_only = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")


def test_a_frame_is_opcode_length_and_json():
    data = rpc.encode(rpc.OP_HANDSHAKE, rpc.handshake("123"))
    op, size = rpc.decode_header(data[:8])
    assert (op, size) == (0, len(data) - 8)
    assert json.loads(data[8:]) == {"v": 1, "client_id": "123"}
    assert data[:8] == struct.pack("<II", 0, size)
    with pytest.raises(ValueError):
        rpc.decode_header(struct.pack("<II", 1, rpc.MAX_FRAME + 1))
    with pytest.raises(ValueError):
        rpc.decode_header(b"1234")
    cmd = rpc.set_activity({"details": "x"}, 42, "n1")
    assert cmd == {"cmd": "SET_ACTIVITY", "nonce": "n1",
                   "args": {"pid": 42, "activity": {"details": "x"}}}
    assert rpc.set_activity(None, 1)["args"]["activity"] is None


def test_where_discord_listens():
    env = {"XDG_RUNTIME_DIR": "/run/user/1000", "TMPDIR": "/var/tmp"}
    paths = rpc.socket_paths(env, "linux")
    assert paths[0] == "/run/user/1000/discord-ipc-0"
    assert "/run/user/1000/app/com.discordapp.Discord/discord-ipc-0" in paths
    assert "/run/user/1000/snap.discord/discord-ipc-0" in paths
    assert "/tmp/discord-ipc-9" in paths and "/var/tmp/discord-ipc-3" in paths
    assert paths.index("/run/user/1000/discord-ipc-0") < paths.index("/tmp/discord-ipc-0")
    win = rpc.socket_paths({}, "win32")
    assert win[0] == r"\\.\pipe\discord-ipc-0" and len(win) == 10


def test_what_the_status_says():
    assert rpc.activity_for(None, True) is None
    live = {"kind": "live", "title": "SVT1 HD", "channel": "SVT1 HD",
            "programme": "Rapport", "started": 1700000000.7}
    act = rpc.activity_for(live, True)
    assert act["type"] == rpc.TYPE_WATCHING
    assert act["details"] == "Watching SVT1 HD" and act["state"] == "Rapport"
    assert act["timestamps"] == {"start": 1700000000}
    assert act["assets"]["large_image"] == rpc.LARGE_IMAGE
    # Names left out: only "Watching TV", whatever plays.
    tv = rpc.activity_for(live, False)
    assert tv["details"] == "Watching TV" and "state" not in tv
    assert tv["timestamps"] == {"start": 1700000000}
    ep = rpc.activity_for({"kind": "episode", "series": "Severance", "title": "Hello",
                           "season": "2", "episode": 4, "started": 1}, True)
    assert ep["details"] == "Watching Severance" and ep["state"] == "S2 E4 · Hello"
    sp = rpc.activity_for({"kind": "episode", "series": "Doctor Who", "title": "",
                           "season": 0, "episode": 1}, True)
    assert sp["state"] == "S0 E1" and "timestamps" not in sp
    film = rpc.activity_for({"kind": "movie", "title": "Arrival", "started": 5}, True,
                            paused=True)
    assert film["details"] == "Watching Arrival" and film["state"] == "Paused"
    assert "timestamps" not in film
    # Discord's limits: 2 to 128 characters, or the line is left out.
    long = rpc.activity_for({"kind": "movie", "title": "x" * 300}, True)
    assert len(long["details"]) == 128 and long["details"].endswith("…")
    short = rpc.activity_for({"kind": "live", "channel": "", "programme": "A"}, True)
    assert short["details"] == "Watching TV" and "state" not in short
    sv = rpc.activity_for(live, True, texts={"watching": "Tittar på {title}"})
    assert sv["details"] == "Tittar på SVT1 HD"


class FakeDiscord:
    """Discord's IPC socket: answers the handshake with READY and every
    SET_ACTIVITY with its echo, and remembers the activities it was given."""

    def __init__(self, path):
        self.path = path
        self.activities = []
        self.handshakes = 0
        self.conns = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(4)
        self.srv.settimeout(0.2)
        self.stopped = False
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while not self.stopped:
            try:
                c, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            self.conns.append(c)
            threading.Thread(target=self.serve, args=(c,), daemon=True).start()

    def frame(self, c):
        head = b""
        while len(head) < 8:
            chunk = c.recv(8 - len(head))
            if not chunk:
                raise ConnectionError
            head += chunk
        op, size = struct.unpack("<II", head)
        body = b""
        while len(body) < size:
            body += c.recv(size - len(body))
        return op, json.loads(body)

    def serve(self, c):
        try:
            op, data = self.frame(c)
            assert op == rpc.OP_HANDSHAKE and data["client_id"] == "4242"
            self.handshakes += 1
            c.sendall(rpc.encode(rpc.OP_FRAME, {"cmd": "DISPATCH", "evt": "READY",
                                                "data": {"v": 1}}))
            while True:
                op, data = self.frame(c)
                if data.get("cmd") == "SET_ACTIVITY":
                    self.activities.append(data["args"]["activity"])
                    c.sendall(rpc.encode(rpc.OP_FRAME, {"cmd": "SET_ACTIVITY",
                                                        "nonce": data["nonce"],
                                                        "evt": None, "data": {}}))
        except (ConnectionError, OSError, ValueError):
            pass

    def quit(self):
        """Discord closing: every connection and the socket go."""
        self.stopped = True
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        self.srv.close()
        os.unlink(self.path)


def _wait(cond, secs=4.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture
def sock_path():
    # A short directory: a Unix socket's path has a length limit.
    d = tempfile.mkdtemp(prefix="dip", dir="/tmp" if sys.platform != "win32" else None)
    yield os.path.join(d, "discord-ipc-0")
    shutil.rmtree(d, ignore_errors=True)


@unix_only
def test_the_status_reaches_discord_and_is_cleared(sock_path):
    fake = FakeDiscord(sock_path)
    p = rpc.Presence("4242", connect=lambda: rpc.open_pipe([sock_path]),
                     retry_s=0.1, pid=7)
    try:
        assert p._thread is None             # nothing shown: no thread at all
        p.show(None)
        assert p._thread is None
        act = {"type": 3, "details": "Watching News"}
        p.show(act)
        assert _wait(lambda: fake.activities == [act])
        assert p.connected and fake.handshakes == 1
        p.show(act)                          # the same again: nothing sent
        p.show(None)
        assert _wait(lambda: fake.activities == [act, None])
    finally:
        p.stop()
        fake.quit()


@unix_only
def test_discord_started_later_and_restarted_is_found_again(sock_path, monkeypatch):
    monkeypatch.setattr(rpc, "CHECK_S", 0.1)
    p = rpc.Presence("4242", connect=lambda: rpc.open_pipe([sock_path]),
                     retry_s=0.1)
    try:
        act = {"type": 3, "details": "Watching Sport"}
        p.show(act)                          # Discord not running yet
        time.sleep(0.3)
        assert not p.connected
        fake = FakeDiscord(sock_path)        # ... and now it is
        assert _wait(lambda: fake.activities == [act])
        fake.quit()                          # Discord quits ...
        assert _wait(lambda: not p.connected)
        again = FakeDiscord(sock_path)       # ... and comes back
        assert _wait(lambda: again.activities == [act])
        again.quit()
    finally:
        p.stop()


def test_showing_never_waits_for_discord():
    started = threading.Event()

    def slow():
        started.set()
        time.sleep(2)
        raise FileNotFoundError

    p = rpc.Presence("1", connect=slow, retry_s=10)
    t0 = time.monotonic()
    p.show({"type": 3, "details": "Watching A"})
    p.show(None)
    p.show({"type": 3, "details": "Watching B"})
    assert started.wait(2)
    # Discord taking its time does not hold up whoever says what plays.
    p.show({"type": 3, "details": "Watching C"})
    p.show(None)
    assert time.monotonic() - t0 < 0.5
    p.stop()


def test_no_discord_is_quiet():
    with pytest.raises(OSError):
        rpc.open_pipe(["/nonexistent-dir-dopeiptv/discord-ipc-0"])


# -- whose socket it is: what plays is said to this user's Discord only -----------

def test_the_user_s_own_folder_is_looked_through_before_tmp():
    env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
    paths = rpc.socket_paths(env, "linux")
    own = [i for i, p in enumerate(paths) if p.startswith("/run/user/1000/")]
    shared = [i for i, p in enumerate(paths) if p.startswith("/tmp/")]
    assert own and shared and max(own) < min(shared)


@unix_only
def test_a_socket_of_another_user_s_is_told_nothing(sock_path, monkeypatch):
    """Discord falls back to /tmp, so /tmp is looked in - where any account
    can make a socket called discord-ipc-0 and wait to be told what this
    user watches."""
    fake = FakeDiscord(sock_path)
    try:
        theirs = os.stat(sock_path).st_uid + 1
        monkeypatch.setattr(rpc.os, "getuid", lambda: theirs)
        assert not rpc.mine(sock_path)
        with pytest.raises(PermissionError):
            rpc.open_pipe([sock_path])
        time.sleep(0.3)
        assert fake.conns == [] and fake.handshakes == 0     # not even connected to
        p = rpc.Presence("4242", connect=lambda: rpc.open_pipe([sock_path]), retry_s=0.05)
        p.show({"type": 3, "details": "Watching News"})
        time.sleep(0.4)
        assert not p.connected and fake.activities == [] and fake.handshakes == 0
        p.stop()
    finally:
        fake.quit()


@unix_only
def test_a_socket_swapped_for_another_user_s_is_told_nothing(sock_path, monkeypatch):
    # The file is this user's when looked at; the one that answers is not.
    fake = FakeDiscord(sock_path)
    try:
        monkeypatch.setattr(rpc, "peer_uid", lambda _sock: os.getuid() + 1)
        with pytest.raises(PermissionError):
            rpc.open_pipe([sock_path])
        time.sleep(0.3)
        assert fake.handshakes == 0 and fake.activities == []
    finally:
        fake.quit()


@unix_only
def test_this_user_s_own_discord_is_found_past_another_s_socket(sock_path, monkeypatch, tmp_path):
    fake = FakeDiscord(sock_path)
    no_socket = str(tmp_path / "discord-ipc-0")
    open(no_socket, "w").close()
    squatted = os.path.join(os.path.dirname(sock_path), "discord-ipc-1")
    squat = FakeDiscord(squatted)
    real_mine = rpc.mine
    monkeypatch.setattr(rpc, "mine", lambda path: path != squatted and real_mine(path))
    try:
        assert not rpc.mine(no_socket)
        pipe = rpc.open_pipe([no_socket, squatted, sock_path])
        pipe.close()
        time.sleep(0.2)
        assert len(fake.conns) == 1 and squat.conns == []
    finally:
        fake.quit()
        squat.quit()


@unix_only
def test_the_system_says_who_is_at_the_other_end():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        uid = rpc.peer_uid(a)
        assert uid is None or uid == os.getuid()
        if sys.platform.startswith("linux") or sys.platform == "darwin":
            assert uid == os.getuid()
    finally:
        a.close()
        b.close()


class _Fn:
    """A function that takes attributes, as a ctypes one does (argtypes)."""

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


def _kernel32(theirs, ours, ok=True):
    def server(_handle, ref):
        ref._obj.value = theirs
        return 1 if ok else 0

    def session(pid, ref):
        assert pid == 4242
        ref._obj.value = ours
        return 1
    k = type("Kernel32", (), {})()
    k.GetNamedPipeServerSessionId = _Fn(server)
    k.ProcessIdToSessionId = _Fn(session)
    k.GetCurrentProcessId = _Fn(lambda: 4242)
    return k


def test_a_windows_pipe_served_from_another_session_is_left_alone():
    """A pipe's name is the whole machine's: another account signed in
    beside this one could make discord-ipc-0 first."""
    assert rpc.pipe_is_ours(7, _kernel32(1, 1)) is True
    assert rpc.pipe_is_ours(7, _kernel32(2, 1)) is False             # another login session
    assert rpc.pipe_is_ours(7, _kernel32(1, 1, ok=False)) is None    # Windows does not say
    assert rpc.pipe_is_ours(7, object()) is None                     # nor does this: as before
