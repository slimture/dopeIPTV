"""Watch together's relay: sealed messages passed between the people in a
room, and nothing else.

The app (dopeiptv/core/together.py on the testing branch) makes a room code
that only the people in the room know, and from it the room id used here
(64 hex) and a key this service never sees. Every message - who is in the
room, what plays, play/pause/seek - is encrypted on the sender's computer
and opened on the others'; here it is a blob of a few hundred bytes. What
the relay knows: a room id, a random id per member and session, the sizes
(padded by the app) and when.

    POST   /together/<room>                    body: one sealed message
                                               (application/octet-stream)
                                               -> 200 {"seq", "now", "n"}
    GET    /together/<room>?after=<seq>&wait=<s>
                                               -> 200 {"seq", "msgs": [[seq, base64], ...],
                                                       "now", "n", "reset"?}
                                               the messages after <seq> (not
                                               the asker's own), waiting up
                                               to MAX_WAIT s for one
    DELETE /together/<room>                    -> 200 {"left": true | false}

Every request names its member (X-Together-Member: 16 to 64 hex, random
in the app). "now" is this server's clock in ms: the app lines positions
up by it. "n" is how many are in the room. 403: the room is full (MAX_MEMBERS
seen in the last MEMBER_SECS, the asker not among them). 503: too many rooms.

In memory only: nothing is written to disk and nothing is logged but
counts. A room keeps its last KEEP_MESSAGES messages (ROOM_BYTES at most)
and is forgotten after IDLE_SECS without a request; a member who has not
asked for MEMBER_SECS gives up their seat. A restart forgets every room -
the app starts over by itself ("reset").
"""

from __future__ import annotations

import base64
import re
import threading
import time
from collections import deque

ROOM = re.compile(r"^[0-9a-f]{64}$")
MEMBER = re.compile(r"^[0-9a-f]{16,64}$")
MEMBER_HEADER = "X-Together-Member"
MAX_MEMBERS = 8
#: The app pads a message to at most 1536 bytes of text; a format byte, a
#: nonce and a tag around it.
MAX_MESSAGE = 2048
MIN_MESSAGE = 1 + 12 + 16
KEEP_MESSAGES = 300
ROOM_BYTES = 128 * 1024
MAX_ROOMS = 500
IDLE_SECS = 3600
MEMBER_SECS = 90
MAX_WAIT = 25.0
#: Long polls held at once: in all, and from one address.
MAX_WAITING = 400
MAX_WAITING_ADDR = 16
SWEEP_SECS = 30


class _Room:
    __slots__ = ("seq", "msgs", "size", "members", "touched", "cond")

    def __init__(self, lock: threading.Lock, now: float) -> None:
        self.seq = 0
        self.msgs: deque = deque()          # (seq, member, blob)
        self.size = 0
        self.members: dict[str, float] = {}
        self.touched = now
        self.cond = threading.Condition(lock)


class Relay:
    """Every room, in memory. Thread-safe: the service answers each request
    on a thread of its own, and a long poll waits on its room."""

    def __init__(self, clock=time.monotonic, wall=time.time) -> None:
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._rooms: dict[str, _Room] = {}
        self._waiting = 0
        self._waiting_by: dict[str, int] = {}
        self._swept = clock()
        self.opened = 0                 # rooms made since the counts were last read

    # -- inside the lock
    def _sweep(self, now: float) -> None:
        if now - self._swept < SWEEP_SECS:
            return
        self._swept = now
        for rid in [r for r, room in self._rooms.items() if now - room.touched > IDLE_SECS]:
            del self._rooms[rid]

    def _room(self, rid: str, now: float) -> _Room | None:
        self._sweep(now)
        room = self._rooms.get(rid)
        if room is None:
            if len(self._rooms) >= MAX_ROOMS:
                return None
            room = self._rooms[rid] = _Room(self._lock, now)
            self.opened += 1
        room.touched = now
        return room

    @staticmethod
    def _seat(room: _Room, member: str, now: float) -> bool:
        for m in [m for m, seen in room.members.items() if now - seen > MEMBER_SECS]:
            del room.members[m]
        if member not in room.members and len(room.members) >= MAX_MEMBERS:
            return False
        room.members[member] = now
        return True

    def _answer(self, room: _Room, **extra) -> dict:
        body = {"seq": room.seq, "now": int(self._wall() * 1000), "n": len(room.members)}
        body.update(extra)
        return body

    # -- the requests
    def post(self, rid: str, member: str, blob: bytes) -> tuple[int, dict]:
        if not ROOM.match(rid or "") or not MEMBER.match(member or ""):
            return 400, {"error": "room or member"}
        if not isinstance(blob, (bytes, bytearray)) or len(blob) > MAX_MESSAGE:
            return 413, {"error": "too big", "most": MAX_MESSAGE}
        if len(blob) < MIN_MESSAGE:
            return 400, {"error": "message"}
        now = self._clock()
        with self._lock:
            room = self._room(rid, now)
            if room is None:
                return 503, {"error": "busy"}
            if not self._seat(room, member, now):
                return 403, {"error": "full", "most": MAX_MEMBERS}
            room.seq += 1
            room.msgs.append((room.seq, member, bytes(blob)))
            room.size += len(blob)
            while room.msgs and (len(room.msgs) > KEEP_MESSAGES or room.size > ROOM_BYTES):
                room.size -= len(room.msgs.popleft()[2])
            room.cond.notify_all()
            return 200, self._answer(room)

    def poll(self, rid: str, member: str, after: int, wait: float,
             addr: str = "") -> tuple[int, dict]:
        if not ROOM.match(rid or "") or not MEMBER.match(member or ""):
            return 400, {"error": "room or member"}
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            return 400, {"error": "after"}
        try:
            wait = max(0.0, min(MAX_WAIT, float(wait)))
        except (TypeError, ValueError):
            return 400, {"error": "wait"}
        now = self._clock()
        deadline = now + wait
        with self._lock:
            room = self._room(rid, now)
            if room is None:
                return 503, {"error": "busy"}
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
                if got or left <= 0:
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
            return 200, self._answer(room, **extra)

    def leave(self, rid: str, member: str) -> tuple[int, dict]:
        if not ROOM.match(rid or "") or not MEMBER.match(member or ""):
            return 400, {"error": "room or member"}
        with self._lock:
            room = self._rooms.get(rid)
            left = room is not None and room.members.pop(member, None) is not None
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
