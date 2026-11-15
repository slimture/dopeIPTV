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
