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
    clock = FakeClock()
    relay = together.Relay(clock=clock)
    for _ in range(together.KEEP_MESSAGES + 50):
        clock.t += 1.1                  # within what one member may post a minute
        assert relay.post(ROOM, A, _blob(64))[0] == 200
    body = relay.poll(ROOM, B, 0, 0)[1]
    assert len(body["msgs"]) == together.KEEP_MESSAGES
    assert body["msgs"][0][0] == 51 and body["seq"] == together.KEEP_MESSAGES + 50
    big = together.Relay(clock=clock)
    for _ in range(200):
        clock.t += 1.1
        assert big.post(ROOM, A, _blob(together.MAX_MESSAGE))[0] == 200
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


KEY_A, KEY_B = "5a" * 32, "6b" * 32          # two rooms' credentials (the app's auth_of)


def test_the_first_credential_binds_a_room_and_nothing_else_gets_in():
    relay = together.Relay()
    assert relay.poll(ROOM, A, 0, 0, auth=KEY_A)[0] == 200                # binds it
    assert relay.post(ROOM, B, _blob(), KEY_A)[0] == 200                  # the same code
    refused = (403, {"error": "refused"})
    # A room id alone opens nothing: no message in, no seat, nothing read.
    for member in (A, "c3" * 8):
        assert relay.post(ROOM, member, _blob()) == refused
        assert relay.post(ROOM, member, _blob(), KEY_B) == refused
        assert relay.poll(ROOM, member, 0, 0) == refused
        assert relay.poll(ROOM, member, 0, 0, auth=KEY_B) == refused
        assert relay.leave(ROOM, member, KEY_B) == refused
    assert relay.leave(ROOM, B) == refused
    status, body = relay.poll(ROOM, A, 0, 0, auth=KEY_A)
    assert status == 200 and body["n"] == 2 and len(body["msgs"]) == 1     # B's, and only B's
    # Only the credential's hash is kept, never the credential.
    room = relay._rooms[ROOM]
    assert room.auth == together._seal(KEY_A) != KEY_A
    assert relay.leave(ROOM, B, KEY_A) == (200, {"left": True})
    # A credential that is no credential is turned down as malformed.
    for bad in ("5A" * 32, "5a" * 31, "x" * 64, "5a" * 33):
        assert relay.poll(ROOM, A, 0, 0, auth=bad)[0] == 400, bad


def test_a_room_of_older_apps_stays_open_and_those_in_it_keep_their_seat():
    clock = FakeClock()
    relay = together.Relay(clock=clock)
    old1, old2, late = "01" * 8, "02" * 20, "03" * 20
    assert relay.poll(ROOM, old1, 0, 0)[0] == 200                         # no credential: as before
    assert relay.post(ROOM, old2, _blob())[0] == 200
    assert "older" not in relay.poll(ROOM, old1, 0, 0)[1]
    # A newer app joins: the room is bound, and those already in it stay.
    status, body = relay.poll(ROOM, A, 0, 0, auth=KEY_A)
    assert status == 200 and body["n"] == 3 and "older" not in body
    assert relay.post(ROOM, old1, _blob())[0] == 200
    assert relay.poll(ROOM, old2, 0, 0)[0] == 200
    # One who comes later without it is turned down - and the member with
    # the credential is told someone was.
    assert relay.poll(ROOM, late, 0, 0) == (403, {"error": "refused"})
    assert relay.poll(ROOM, A, 0, 0, auth=KEY_A)[1]["older"] is True
    assert "older" not in relay.poll(ROOM, old1, 0, 0)[1]               # an older app is not told
    clock.t += 60
    assert relay.poll(ROOM, old1, 0, 0)[0] == 200                       # still seated: still in
    clock.t += 40
    assert "older" not in relay.poll(ROOM, A, 0, 0, auth=KEY_A)[1]      # a while ago now
    # A seat given up - silent too long, or left - is not given back without it.
    assert relay.poll(ROOM, old2, 0, 0) == (403, {"error": "refused"})
    assert relay.leave(ROOM, old1) == (200, {"left": True})
    assert relay.post(ROOM, old1, _blob()) == (403, {"error": "refused"})
    # A room no newer app ever came to is never bound.
    other = "ef" * 32
    for _ in range(3):
        assert relay.post(other, old1, _blob())[0] == 200
    assert relay._rooms[other].auth == ""


def test_a_member_posts_so_many_messages_a_minute():
    clock = FakeClock()
    relay = together.Relay(clock=clock)
    for _ in range(together.MAX_PER_MINUTE):
        assert relay.post(ROOM, A, _blob(), KEY_A)[0] == 200
    assert relay.post(ROOM, A, _blob(), KEY_A) == (429, {"error": "slow down"})
    assert relay.post(ROOM, B, _blob(), KEY_A)[0] == 200                  # another member
    assert relay.poll(ROOM, A, 0, 0, auth=KEY_A)[0] == 200                # reading goes on
    clock.t += 60
    assert relay.post(ROOM, A, _blob(), KEY_A)[0] == 200
    assert relay.poll(ROOM, B, 0, 0, auth=KEY_A)[1]["seq"] == together.MAX_PER_MINUTE + 2


def test_one_address_keeps_so_many_rooms_and_a_room_lives_a_day_at_the_most():
    clock = FakeClock()
    relay = together.Relay(clock=clock)
    ip, other = "203.0.113.30", "203.0.113.31"
    rooms = [f"{i:064x}" for i in range(together.ROOMS_PER_ADDR + 1)]
    for rid in rooms[:-1]:
        assert relay.post(rid, A, _blob(), KEY_A, ip)[0] == 200
    assert relay.post(rooms[-1], A, _blob(), KEY_A, ip) == (429, {"error": "slow down"})
    assert relay.poll(rooms[-1], A, 0, 0, ip, KEY_A)[0] == 429
    assert relay.post(rooms[0], A, _blob(), KEY_A, ip)[0] == 200          # its own rooms go on
    assert relay.post(rooms[-1], A, _blob(), KEY_A, other)[0] == 200      # another address
    # The rooms keep no address: only a hash under this run's own key.
    assert all(ip not in r.opener and r.opener for r in relay._rooms.values())
    assert relay._tag(ip) != together.Relay()._tag(ip)
    # A day after it was made a room is forgotten however busy it is: its
    # messages, seats and binding with it, and the address's count.
    born = relay._rooms[rooms[0]].born
    while clock.t + 1800 - born <= together.LIFETIME_SECS:
        clock.t += 1800
        assert relay.post(rooms[0], A, _blob(), KEY_A, ip)[0] == 200
    assert relay._rooms[rooms[0]].born == born and len(relay._rooms) == 1   # the idle ones went
    clock.t = born + together.LIFETIME_SECS + 1
    status, body = relay.poll(rooms[0], B, 10, 0, ip, KEY_B)
    assert status == 200 and body["reset"] is True and body["n"] == 1 and body["msgs"] == []
    assert relay._rooms[rooms[0]].auth == together._seal(KEY_B)
    assert relay._made_by == {relay._tag(ip): 1}


def test_a_long_poll_on_a_room_forgotten_answers_and_the_next_starts_over():
    clock = FakeClock()
    relay = together.Relay(clock=clock)
    relay.post(ROOM, A, _blob(), KEY_A)
    out = {}

    def waiter():
        out["got"] = relay.poll(ROOM, B, 1, 5, auth=KEY_A)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.3)
    relay._rooms[ROOM].born -= together.LIFETIME_SECS + 1      # its time is up, the clock still
    t0 = time.monotonic()
    relay.poll(ROOM, A, 0, 0, auth=KEY_A)                                 # makes the room anew
    t.join(5)
    assert not t.is_alive() and time.monotonic() - t0 < 2 and out["got"][0] == 200
    assert relay.poll(ROOM, B, 1, 0, auth=KEY_A)[1]["reset"] is True


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


def test_one_address_cannot_open_every_room():
    """Asking for a room makes it. An address opens only so many in a while;
    the rooms it has are still answered, and so is another address."""
    srv, _stats, donate = _service()
    try:
        ip = "203.0.113.20"
        donate.TOGETHER_NEW_PER_WINDOW = 3
        rooms = [f"{i:02x}" * 32 for i in range(0x30, 0x35)]
        codes = [_call(srv, "GET", f"/together/{r}?after=0&wait=0", A, ip=ip)[0] for r in rooms]
        assert codes == [200, 200, 200, 429, 429]
        assert _call(srv, "GET", f"/together/{rooms[0]}?after=0&wait=0", A, ip=ip)[0] == 200
        assert _call(srv, "POST", f"/together/{rooms[4]}", A, _blob(100), ip=ip)[0] == 429
        assert _call(srv, "GET", f"/together/{rooms[4]}?after=0&wait=0", A,
                     ip="203.0.113.21")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_route_takes_the_credential_and_counts_an_ipv6_address_by_its_64():
    srv, stats, donate = _service()
    try:
        path = f"/together/{ROOM}"
        auth = {together.AUTH_HEADER: KEY_A}
        assert _call(srv, "POST", path, A, _blob(100), headers=auth)[0] == 200
        assert _call(srv, "GET", path + "?after=0&wait=0", B) == (403, {"error": "refused"})
        assert _call(srv, "GET", path + "?after=0&wait=0", B,
                     headers={together.AUTH_HEADER: KEY_A.upper()})[0] == 200
        assert _call(srv, "GET", path + "?after=0&wait=0", B,
                     headers={together.AUTH_HEADER: "nope"})[0] == 400
        assert _call(srv, "DELETE", path, B)[0] == 403
        assert _call(srv, "DELETE", path, B, headers=auth) == (200, {"left": True})
        # The addresses of one /64 are one asker to the limits.
        donate.TOGETHER_PER_WINDOW = 4
        codes = [_call(srv, "GET", path + "?after=0&wait=0", A, ip=f"2001:db8:5:6::{i:x}",
                       headers=auth)[0] for i in range(1, 6)]
        assert codes == [200] * 4 + [429]
        assert _call(srv, "GET", path + "?after=0&wait=0", A, ip="2001:db8:5:7::1",
                     headers=auth)[0] == 200
        # Nothing on disk: not the room, its credential or an address.
        for dirpath, _dirs, files in os.walk(stats):
            assert not files, (dirpath, files)
    finally:
        srv.shutdown()
        srv.server_close()


def test_where_an_address_counts():
    assert together.where("203.0.113.9") == "203.0.113.9"
    assert together.where("2001:db8:5:6::1") == together.where("2001:db8:5:6:ffff::9") == "2001:db8:5:6::/64"
    assert together.where("::ffff:203.0.113.9") == "203.0.113.9"
    assert together.where("local") == "local" and together.where("") == ""


def test_a_room_nobody_spoke_in_goes_soon_and_makes_way_when_full():
    clock = FakeClock()
    relay = together.Relay(clock=clock, wall=lambda: 0.0)
    old = together.MAX_ROOMS
    together.MAX_ROOMS = 3
    try:
        spoken, silent = "aa" * 32, ["b%d" % i * 32 for i in range(2)]
        assert relay.post(spoken, A, _blob(100))[0] == 200
        for rid in silent:
            assert relay.poll(rid, A, 0, 0)[0] == 200
        assert relay.poll("cc" * 32, A, 0, 0)[0] == 503             # full
        clock.t += together.EMPTY_SECS + 1
        assert relay.poll("cc" * 32, A, 0, 0)[0] == 200             # the silent ones went
        assert relay.has(spoken) and not relay.has(silent[0])
    finally:
        together.MAX_ROOMS = old


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
