#!/usr/bin/env python3
"""Tests for watch together's relay (together.py, and its routes in donate.py).

Plain Python: ``python3 donate/test_together.py`` runs them all (pytest finds
them too: ``python3 -m pytest donate/test_together.py``). In memory only;
the service on 127.0.0.1 for the HTTP part.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import together  # noqa: E402

ROOM = "cd" * 32
A, B = "a1" * 8, "b2" * 8


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _blob(n: int = 200) -> bytes:
    return secrets.token_bytes(n)


def test_messages_pass_in_order_and_not_back_to_their_sender():
    relay = together.Relay()
    one, two = _blob(), _blob()
    assert relay.post(ROOM, A, one)[0] == 200
    status, body = relay.post(ROOM, B, two)
    assert status == 200 and body["seq"] == 2 and body["n"] == 2 and body["now"] > 0
    status, body = relay.poll(ROOM, A, 0, 0)
    assert status == 200 and body["seq"] == 2
    assert [(s, base64.b64decode(m)) for s, m in body["msgs"]] == [(2, two)]     # not A's own
    status, body = relay.poll(ROOM, B, 0, 0)
    assert [(s, base64.b64decode(m)) for s, m in body["msgs"]] == [(1, one)]
    status, body = relay.poll(ROOM, B, 2, 0)
    assert body["msgs"] == [] and "reset" not in body


def test_a_long_poll_waits_and_wakes_on_a_message():
    relay = together.Relay()
    relay.poll(ROOM, A, 0, 0)
    out = {"after": 0}

    def waiter():
        t0 = time.monotonic()
        out["got"] = relay.poll(ROOM, A, out["after"], 5)
        out["took"] = time.monotonic() - t0

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.3)
    assert relay.counts()["waiting"] == 1
    relay.post(ROOM, B, _blob())
    t.join(5)
    status, body = out["got"]
    assert status == 200 and len(body["msgs"]) == 1 and out["took"] < 3
    # Nothing new: it waits for as long as asked, then answers empty.
    t0 = time.monotonic()
    status, body = relay.poll(ROOM, A, body["seq"], 0.4)
    assert status == 200 and body["msgs"] == [] and time.monotonic() - t0 >= 0.35
    # Its own message does not wake it.
    out.clear()
    out["after"] = body["seq"]
    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.2)
    relay.post(ROOM, A, _blob())
    t.join(0.5)
    assert t.is_alive()
    relay.post(ROOM, B, _blob())
    t.join(5)
    assert not t.is_alive() and len(out["got"][1]["msgs"]) == 1


def test_at_most_eight_and_a_seat_is_given_up():
    clock = FakeClock()
    relay = together.Relay(clock=clock)
    members = [f"{i:016x}" for i in range(together.MAX_MEMBERS)]
    for m in members:
        assert relay.poll(ROOM, m, 0, 0)[0] == 200
    ninth = "f" * 16
    assert relay.poll(ROOM, ninth, 0, 0) == (403, {"error": "full", "most": together.MAX_MEMBERS})
    assert relay.post(ROOM, ninth, _blob())[0] == 403
    assert relay.leave(ROOM, members[0]) == (200, {"left": True})
    assert relay.leave(ROOM, members[0]) == (200, {"left": False})
    assert relay.poll(ROOM, ninth, 0, 0)[0] == 200                    # the seat it left
    assert relay.poll(ROOM, members[0], 0, 0)[0] == 403
    clock.t += together.MEMBER_SECS + 1                                # all silent: seats free
    assert relay.poll(ROOM, members[0], 0, 0)[0] == 200
    assert relay.poll(ROOM, members[0], 0, 0)[1]["n"] == 1


def test_a_room_keeps_its_last_messages_only():
    relay = together.Relay()
    for _ in range(together.KEEP_MESSAGES + 50):
        relay.post(ROOM, A, _blob(64))
    body = relay.poll(ROOM, B, 0, 0)[1]
    assert len(body["msgs"]) == together.KEEP_MESSAGES
    assert body["msgs"][0][0] == 51 and body["seq"] == together.KEEP_MESSAGES + 50
    big = together.Relay()
    for _ in range(200):
        big.post(ROOM, A, _blob(together.MAX_MESSAGE))
    kept = big.poll(ROOM, B, 0, 0)[1]["msgs"]
    assert sum(len(base64.b64decode(m)) for _s, m in kept) <= together.ROOM_BYTES


def test_rooms_are_forgotten_and_a_newer_seq_starts_over():
    clock = FakeClock()
    relay = together.Relay(clock=clock)
    relay.post(ROOM, A, _blob())
    relay.post(ROOM, A, _blob())
    clock.t += together.IDLE_SECS + together.SWEEP_SECS + 1
    other = "ef" * 32
    relay.poll(other, B, 0, 0)                                         # any request sweeps
    assert relay.counts()["rooms"] == 1
    status, body = relay.poll(ROOM, B, 2, 0)                           # B was at 2: gone
    assert status == 200 and body["reset"] is True and body["seq"] == 0 and body["msgs"] == []
    relay.post(ROOM, A, _blob())
    status, body = relay.poll(ROOM, B, 5, 0)
    assert body["reset"] is True and [s for s, _m in body["msgs"]] == [1]


def test_what_is_refused():
    relay = together.Relay()
    for room in ("", "CD" * 32, "cd" * 31, "../" + "a" * 61, "g" * 64):
        assert relay.post(room, A, _blob())[0] == 400, room
    for member in ("", "A1" * 8, "a1" * 4, "x" * 16, "a" * 65):
        assert relay.post(ROOM, member, _blob())[0] == 400, member
    assert relay.post(ROOM, A, _blob(together.MIN_MESSAGE - 1))[0] == 400
    assert relay.post(ROOM, A, _blob(together.MAX_MESSAGE + 1))[0] == 413
    assert relay.post(ROOM, A, _blob(together.MAX_MESSAGE))[0] == 200
    assert relay.post(ROOM, A, "text")[0] == 413
    assert relay.poll(ROOM, A, -1, 0)[0] == 400
    assert relay.poll(ROOM, A, True, 0)[0] == 400
    assert relay.poll(ROOM, A, 0, "x")[0] == 400


def test_too_many_rooms_and_waiters():
    relay = together.Relay()
    for i in range(together.MAX_ROOMS):
        assert relay.poll(f"{i:064x}", A, 0, 0)[0] == 200
    assert relay.post("f" * 64, A, _blob()) == (503, {"error": "busy"})
    # An address holding its share of long polls is answered at once.
    relay = together.Relay()
    relay._waiting_by["198.51.100.1"] = together.MAX_WAITING_ADDR
    t0 = time.monotonic()
    assert relay.poll(ROOM, A, 0, 10, "198.51.100.1")[0] == 200
    assert time.monotonic() - t0 < 1


def test_counts_say_nothing_but_counts():
    relay = together.Relay()
    relay.post(ROOM, A, _blob())
    relay.poll(ROOM, B, 0, 0)
    seen = relay.counts()
    assert seen == {"rooms": 1, "members": 2, "waiting": 0, "opened": 1}
    assert relay.counts()["opened"] == 0


# -- the routes in donate.py ----------------------------------------------------------

def _service():
    root = tempfile.mkdtemp()
    conf = os.path.join(root, "donate.conf")
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nLISTEN=127.0.0.1:0\n")
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    sys.modules.pop("donate", None)
    import donate
    from http.server import ThreadingHTTPServer
    together.RELAY = together.Relay()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), donate.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, stats, donate


def _call(srv, method, path, member=A, body=None, ip="198.51.100.7", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=40)
    h = {"X-Real-IP": ip}
    if member is not None:
        h[together.MEMBER_HEADER] = member
    if body is not None:
        h["Content-Type"] = "application/octet-stream"
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:
        return r.status, {}


def test_the_routes_pass_blobs_and_keep_nothing():
    srv, stats, _donate = _service()
    try:
        path = "/together/" + ROOM
        blob = _blob(300)
        status, body = _call(srv, "POST", path, A, blob)
        assert status == 200 and body["seq"] == 1 and body["n"] == 1
        assert abs(body["now"] / 1000 - time.time()) < 5
        status, body = _call(srv, "GET", path + "?after=0&wait=0", B)
        assert status == 200 and base64.b64decode(body["msgs"][0][1]) == blob and body["n"] == 2
        assert _call(srv, "GET", path + "?after=0&wait=0", None)[0] == 400       # no member
        assert _call(srv, "GET", "/together/xyz?after=0", B)[0] == 404
        assert _call(srv, "GET", path + "?after=x", B)[0] == 400
        assert _call(srv, "GET", path + "?after=0&wait=nan", B)[0] == 400
        assert _call(srv, "POST", path, A, b"")[0] == 400
        assert _call(srv, "POST", path, A, _blob(together.MAX_MESSAGE + 1))[0] == 413
        assert _call(srv, "PUT", path, A, blob)[0] == 404
        # A long poll through the service, woken by a message.
        out = {}

        def waiter():
            out["got"] = _call(srv, "GET", path + "?after=1&wait=10", B)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.4)
        two = _blob(100)
        assert _call(srv, "POST", path, A, two)[0] == 200
        t.join(10)
        status, body = out["got"]
        assert status == 200 and [base64.b64decode(m) for _s, m in body["msgs"]] == [two]
        assert _call(srv, "DELETE", path, B) == (200, {"left": True})
        # Nothing on disk: not a room, not a member, not an address.
        for dirpath, _dirs, files in os.walk(stats):
            assert not files, (dirpath, files)
    finally:
        srv.shutdown()
        srv.server_close()


def test_an_address_is_slowed_down():
    srv, _stats, donate = _service()
    try:
        ip = "203.0.113.9"
        donate.TOGETHER_PER_WINDOW = 15
        codes = [_call(srv, "GET", f"/together/{ROOM}?after=0&wait=0", A, ip=ip)[0]
                 for _ in range(16)]
        assert codes[:-1] == [200] * 15 and codes[-1] == 429
        assert _call(srv, "GET", f"/together/{ROOM}?after=0&wait=0", A, ip="203.0.113.10")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
