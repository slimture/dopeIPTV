"""Watch together's relay: sealed messages passed between the people in a
room, and nothing else.

The app (dopeiptv/core/together.py on the testing branch) makes a room code
that only the people in the room know, and from it the room id used here
(64 hex), the room's credential and a key this service never sees. Every
message - who is in the room, what plays, play/pause/seek - is encrypted on
the sender's computer and opened on the others'; here it is a blob of a few
hundred bytes. What the relay knows: a room id, an id per member and
session (the hash of a key the member made for the room), the sizes
(padded by the app) and when.

    POST   /together/<room>                    body: one sealed message
                                               (application/octet-stream)
                                               -> 200 {"seq", "now", "n", "older"?}
    GET    /together/<room>?after=<seq>&wait=<s>
                                               -> 200 {"seq", "msgs": [[seq, base64], ...],
                                                       "now", "n", "reset"?, "older"?}
                                               the messages after <seq> (not
                                               the asker's own), waiting up
                                               to MAX_WAIT s for one
    DELETE /together/<room>                    -> 200 {"left": true | false}

Every request names its member (X-Together-Member: 16 to 64 hex) and, from
the app's 1.6.11, carries the room's credential (X-Together-Auth: 64 hex,
made from the same code as the room id, under a label of its own). Of it
only its SHA-256 is kept, in memory beside the room: the first request with
one binds the room to it, and from then on a request without it - to post,
to read or to leave - is answered 403 {"error": "refused"}, and nothing of
the room is said. A room id alone then opens nothing: it puts no message in
the room, takes no seat and reads none. A room only older apps use (they
send none) stays unbound, as before. Those of them seated when a newer app
binds it keep their seat while they go on asking (the room's "open"); one
that comes later without the credential is turned down, and for
MEMBER_SECS the answers to the members with it say "older", so their apps
can say that someone on an older app was left out.

"now" is this server's clock in ms: the app lines positions up by it. "n"
is how many are in the room. 403: the room is full (MAX_MEMBERS seen in the
last MEMBER_SECS, the asker not among them) or refused. 429: the member
posted MAX_PER_MINUTE messages in the last minute, or the address has
ROOMS_PER_ADDR rooms kept already. 503: too many rooms.

In memory only: nothing is written to disk and nothing is logged but
counts. A room keeps its last KEEP_MESSAGES messages (ROOM_BYTES at most)
and is forgotten after IDLE_SECS without a request (EMPTY_SECS if nobody
ever said anything in it), and LIFETIME_SECS after it was made however busy
it is; a member who has not asked for MEMBER_SECS gives up their seat. The
next request makes a forgotten room again, empty and unbound, and the app
goes on by itself ("reset"), as after a restart, which forgets every room.
To count how many rooms an address has made, a room keeps a keyed hash of
it (the key random, this run's alone, never written) - never the address.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from collections import deque

ROOM = re.compile(r"^[0-9a-f]{64}$")
MEMBER = re.compile(r"^[0-9a-f]{16,64}$")
#: The room's credential as a request carries it.
AUTH = re.compile(r"^[0-9a-f]{64}$")
MEMBER_HEADER = "X-Together-Member"
AUTH_HEADER = "X-Together-Auth"
MAX_MEMBERS = 8
#: The app pads a message to at most 1536 bytes of text; a format byte, a
#: nonce and a tag around it.
MAX_MESSAGE = 2048
MIN_MESSAGE = 1 + 12 + 16
KEEP_MESSAGES = 300
ROOM_BYTES = 128 * 1024
MAX_ROOMS = 500
IDLE_SECS = 3600
#: A room nobody ever said anything in goes sooner: asking for a room that
#: is not there makes it, and MAX_ROOMS of those held the relay for an hour.
EMPTY_SECS = 300
#: However busy, a room is forgotten this long after it was made: its
#: messages, its seats and its credential's hash live a day at the most.
LIFETIME_SECS = 24 * 3600
MEMBER_SECS = 90
#: What one member posts in a room in a minute: the app says a few things a
#: minute (hellos and their answers, a keepalive, a play or a pause).
MAX_PER_MINUTE = 60
#: Rooms one address has made that are kept at once. Far above a household's
#: rooms; many people can share one address (a carrier's NAT).
ROOMS_PER_ADDR = 20
MAX_WAIT = 25.0
#: Long polls held at once: in all, and from one address.
MAX_WAITING = 400
MAX_WAITING_ADDR = 16
SWEEP_SECS = 30


def _seal(auth: str) -> str:
    """What is kept of a room's credential: its SHA-256."""
    return hashlib.sha256(auth.encode("ascii")).hexdigest()


def where(addr: str) -> str:
    """An address as the limits count it: an IPv6 address by its /64 - a
    connection is given a whole /64, and every address in it is the same
    asker's."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class _Room:
    __slots__ = ("seq", "msgs", "size", "members", "touched", "cond", "born", "auth", "open",
                 "sent", "refused", "opener")

    def __init__(self, lock: threading.Lock, now: float, opener: str = "") -> None:
        self.seq = 0
        self.msgs: deque = deque()          # (seq, member, blob)
        self.size = 0
        self.members: dict[str, float] = {}
        self.touched = now
        self.cond = threading.Condition(lock)
        self.born = now
        self.auth = ""                      # the credential's SHA-256, once bound
        self.open: set[str] = set()         # seated without one when it was bound
        self.sent: dict[str, deque] = {}    # each member's posts of the last minute
        self.refused = float("-inf")        # one without the credential turned down
        self.opener = opener                # the keyed hash of the address that made it


class Relay:
    """Every room, in memory. Thread-safe: the service answers each request
    on a thread of its own, and a long poll waits on its room."""

    def __init__(self, clock=time.monotonic, wall=time.time) -> None:
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._rooms: dict[str, _Room] = {}
        self._waiting = 0
        self._waiting_by: dict[str, int] = {}
        self._made_by: dict[str, int] = {}
        self._tag_key = secrets.token_bytes(32)
        self._swept = clock()
        self.opened = 0                 # rooms made since the counts were last read

    # -- inside the lock
    def _tag(self, addr: str) -> str:
        """An address as a room keeps it: a hash under this run's own key."""
        if not addr:
            return ""
        return hmac.new(self._tag_key, addr.encode("utf-8", "replace"), hashlib.sha256).hexdigest()

    def _drop(self, rid: str) -> None:
        room = self._rooms.pop(rid, None)
        if room is None:
            return
        if room.opener:
            n = self._made_by.get(room.opener, 1) - 1
            if n > 0:
                self._made_by[room.opener] = n
            else:
                self._made_by.pop(room.opener, None)
        room.cond.notify_all()          # its long polls answer now; the next finds the room anew

    @staticmethod
    def _over(room: _Room, now: float) -> bool:
        return (now - room.touched > (IDLE_SECS if room.msgs else EMPTY_SECS)
                or now - room.born > LIFETIME_SECS)

    def _sweep(self, now: float, force: bool = False) -> None:
        if not force and now - self._swept < SWEEP_SECS:
            return
        self._swept = now
        for rid in [r for r, room in self._rooms.items() if self._over(room, now)]:
            self._drop(rid)

    def has(self, rid: str) -> bool:
        """Whether room *rid* is kept now (the service limits how many new
        ones an address opens)."""
        with self._lock:
            return rid in self._rooms

    def _room(self, rid: str, now: float, addr: str = "") -> tuple[_Room | None, int]:
        """The room, made if need be: (room, 200), or (None, 503) when there
        are MAX_ROOMS, (None, 429) when *addr* has made ROOMS_PER_ADDR."""
        self._sweep(now)
        room = self._rooms.get(rid)
        if room is not None and now - room.born > LIFETIME_SECS:
            self._drop(rid)
            room = None
        if room is None:
            if len(self._rooms) >= MAX_ROOMS:
                self._sweep(now, force=True)
            if len(self._rooms) >= MAX_ROOMS:
                return None, 503
            tag = self._tag(addr)
            if tag and self._made_by.get(tag, 0) >= ROOMS_PER_ADDR:
                return None, 429
            room = self._rooms[rid] = _Room(self._lock, now, tag)
            if tag:
                self._made_by[tag] = self._made_by.get(tag, 0) + 1
            self.opened += 1
        return room, 200

    @staticmethod
    def _expire(room: _Room, now: float) -> None:
        for m in [m for m, seen in room.members.items() if now - seen > MEMBER_SECS]:
            del room.members[m]
            room.open.discard(m)
            room.sent.pop(m, None)

    def _admit(self, room: _Room, member: str, auth: str, now: float, bind: bool = True) -> bool:
        """Whether *member* may use *room*: an unbound room takes anyone, and
        the first credential binds it (*bind*); a bound one takes its own
        credential, or a member seated without one before it was bound, for
        as long as it keeps its seat. Compared in constant time."""
        self._expire(room, now)
        if not room.auth:
            if auth and bind:
                room.auth = _seal(auth)
                room.open = set(room.members) - {member}
            return True
        if auth:
            return hmac.compare_digest(_seal(auth), room.auth)
        if member in room.open:
            return True
        room.refused = now
        return False

    @staticmethod
    def _seat(room: _Room, member: str, now: float) -> bool:
        if member not in room.members and len(room.members) >= MAX_MEMBERS:
            return False
        room.members[member] = now
        return True

    @staticmethod
    def _paced(room: _Room, member: str, now: float) -> bool:
        """Whether *member* may post now: MAX_PER_MINUTE in a minute."""
        times = room.sent.setdefault(member, deque())
        while times and now - times[0] >= 60.0:
            times.popleft()
        if len(times) >= MAX_PER_MINUTE:
            return False
        times.append(now)
        return True

    def _answer(self, room: _Room, auth: str = "", **extra) -> dict:
        body = {"seq": room.seq, "now": int(self._wall() * 1000), "n": len(room.members)}
        if auth and self._clock() - room.refused <= MEMBER_SECS:
            body["older"] = True
        body.update(extra)
        return body

    @staticmethod
    def _checked(rid: str, member: str, auth: str) -> tuple[int, dict] | None:
        if not ROOM.match(rid or "") or not MEMBER.match(member or ""):
            return 400, {"error": "room or member"}
        if auth and not AUTH.match(auth):
            return 400, {"error": "auth"}
        return None

    # -- the requests
    def post(self, rid: str, member: str, blob: bytes, auth: str = "",
             addr: str = "") -> tuple[int, dict]:
        bad = self._checked(rid, member, auth)
        if bad:
            return bad
        if not isinstance(blob, (bytes, bytearray)) or len(blob) > MAX_MESSAGE:
            return 413, {"error": "too big", "most": MAX_MESSAGE}
        if len(blob) < MIN_MESSAGE:
            return 400, {"error": "message"}
        now = self._clock()
        with self._lock:
            room, status = self._room(rid, now, addr)
            if room is None:
                return status, {"error": "busy" if status == 503 else "slow down"}
            if not self._admit(room, member, auth, now):
                return 403, {"error": "refused"}
            room.touched = now
            if not self._seat(room, member, now):
                return 403, {"error": "full", "most": MAX_MEMBERS}
            if not self._paced(room, member, now):
                return 429, {"error": "slow down"}
            room.seq += 1
            room.msgs.append((room.seq, member, bytes(blob)))
            room.size += len(blob)
            while room.msgs and (len(room.msgs) > KEEP_MESSAGES or room.size > ROOM_BYTES):
                room.size -= len(room.msgs.popleft()[2])
            room.cond.notify_all()
            return 200, self._answer(room, auth)

    def poll(self, rid: str, member: str, after: int, wait: float,
             addr: str = "", auth: str = "") -> tuple[int, dict]:
        bad = self._checked(rid, member, auth)
        if bad:
            return bad
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            return 400, {"error": "after"}
        try:
            wait = max(0.0, min(MAX_WAIT, float(wait)))
        except (TypeError, ValueError):
            return 400, {"error": "wait"}
        now = self._clock()
        deadline = now + wait
        with self._lock:
            room, status = self._room(rid, now, addr)
            if room is None:
                return status, {"error": "busy" if status == 503 else "slow down"}
            if not self._admit(room, member, auth, now):
                return 403, {"error": "refused"}
            room.touched = now
            if not self._seat(room, member, now):
                return 403, {"error": "full", "most": MAX_MEMBERS}
            reset = after > room.seq
            if reset:
                after = 0
            if wait and (self._waiting >= MAX_WAITING
                         or self._waiting_by.get(addr, 0) >= MAX_WAITING_ADDR):
                deadline = now                # answered at once; the app waits a little itself
            while True:
                got = [(s, b) for s, m, b in room.msgs if s > after and m != member]
                left = deadline - self._clock()
                if got or left <= 0 or self._rooms.get(rid) is not room:
                    break
                self._waiting += 1
                self._waiting_by[addr] = self._waiting_by.get(addr, 0) + 1
                try:
                    room.cond.wait(left)
                finally:
                    self._waiting -= 1
                    n = self._waiting_by.get(addr, 1) - 1
                    if n > 0:
                        self._waiting_by[addr] = n
                    else:
                        self._waiting_by.pop(addr, None)
                t = self._clock()
                room.touched = t
                if member in room.members:
                    room.members[member] = t
            extra: dict = {"msgs": [[s, base64.b64encode(b).decode("ascii")] for s, b in got]}
            if reset:
                extra["reset"] = True
            return 200, self._answer(room, auth, **extra)

    def leave(self, rid: str, member: str, auth: str = "") -> tuple[int, dict]:
        bad = self._checked(rid, member, auth)
        if bad:
            return bad
        with self._lock:
            room = self._rooms.get(rid)
            if room is None:
                return 200, {"left": False}
            if not self._admit(room, member, auth, self._clock(), bind=False):
                return 403, {"error": "refused"}
            left = room.members.pop(member, None) is not None
            room.open.discard(member)
            room.sent.pop(member, None)
            return 200, {"left": left}

    def counts(self) -> dict:
        """How many rooms, members and waiting requests there are now, and
        rooms made since the last time this was asked - all that is ever
        told of the rooms."""
        with self._lock:
            now = self._clock()
            rooms = [r for r in self._rooms.values() if now - r.touched <= IDLE_SECS]
            members = sum(len([1 for seen in r.members.values() if now - seen <= MEMBER_SECS])
                          for r in rooms)
            opened, self.opened = self.opened, 0
            return {"rooms": len(rooms), "members": members, "waiting": self._waiting,
                    "opened": opened}


RELAY = Relay()


if __name__ == "__main__":
    print(__doc__)
