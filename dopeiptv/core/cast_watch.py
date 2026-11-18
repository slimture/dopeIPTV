"""A running cast watched: what the receiver says it does, as the cast card
says it, and the watchdog that sends a stuck cast to the same TV again.

Every receiver reports its state as "<STATE>/<reason>" (providers/
chromecast.py, dlna.py, kodi.py, services/airplay_macos.py): PLAYING,
BUFFERING, PAUSED, IDLE with FINISHED or ERROR, and AirPlay's WAITING/ROUTE
until an Apple TV is picked - in whatever case the receiver spells it
("PLAYING/None", "BUFFERING/NONE"). ``phase`` reads one as a word of the
card's.

``Watchdog`` is asked once a second (the cast's own tick). Buffering - or a
receiver that has stopped with an error - going on for ``STALL_S`` while the
person did not pause it and nothing is on its way to the receiver is a
stall: ``RECOVER``, the window sends the stream to the same TV again from
the same place. At most ``TRIES`` times for one stall, then ``FAIL`` (the
card's Try again). Playing again for ``SETTLED_S`` ends it: the next stall
has its own tries. A buffering that ends by itself is told too
(``RESOLVED``), and how long it took (``last_stall``).

Pure: no Qt, the clock passed in.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The card's words for what the receiver does.
PLAYING, BUFFERING, PAUSED, WAITING = "playing", "buffering", "paused", "waiting"
ENDED, ERROR, IDLE, STARTING = "ended", "error", "idle", "starting"

#: Buffering this long, not paused by the person, is a stall.
STALL_S = 10.0
#: A stream that has not played yet - a cast just begun, a seek, a channel
#: sent again - is given this long to start: an Apple TV loading a new
#: stream often takes more than STALL_S, and sending it again then only
#: started it over (it went back a few seconds, and a seek never took).
START_STALL_S = 45.0
#: The tries for one stall before the card says it failed.
TRIES = 2
#: Playing this long after a try: the stall is over, and so are its tries.
SETTLED_S = 5.0
#: "Buffering…" stays on the card at least this long, even when it ended.
SHOW_BUFFERING_S = 2.0

#: What ``Watchdog.tick`` says happened (or "" for nothing worth a word).
BEGAN, RESOLVED, RECOVER, RECOVERED, FAIL = "began", "resolved", "recover", "recovered", "fail"


def phase(raw: object) -> str:
    """A receiver's "<STATE>/<reason>" as one of the card's words."""
    text = str(raw or "").strip().upper()
    head, _sep, why = text.partition("/")
    if head == "PLAYING":
        return PLAYING
    if head == "BUFFERING":
        return BUFFERING
    if head == "PAUSED":
        return PAUSED
    if head == "WAITING":
        return WAITING
    if head == "IDLE":
        if why == "FINISHED":
            return ENDED
        if why == "ERROR":
            return ERROR
        return IDLE
    return STARTING


@dataclass
class Watchdog:
    """One cast's stalls (a new title or channel on the TV is a new one)."""

    stall_s: float = STALL_S
    start_stall_s: float = START_STALL_S
    tries_max: int = TRIES
    settled_s: float = SETTLED_S
    #: Stuck since (monotonic seconds), while it is.
    since: float | None = None
    #: The tries sent for the stall now.
    tries: int = 0
    #: Given up on: the card's Try again starts over (``reset``).
    failed: bool = False
    #: Playing since, while it plays.
    playing_since: float | None = None
    #: When the receiver last said it was buffering.
    buffered_at: float | None = None
    #: How long the last stall lasted, once it ended.
    last_stall: float = 0.0
    #: The stream on the receiver has played since it was sent.
    started: bool = False

    def reset(self) -> None:
        """Try again: a stall of its own, its own tries."""
        self.since = self.playing_since = self.buffered_at = None
        self.tries = 0
        self.failed = False
        self.started = False

    def new_stream(self) -> None:
        """A stream sent to the receiver (a seek, live, a try): it has
        START_STALL_S to start before a wait is a stall; the tries stay."""
        self.since = self.playing_since = None
        self.started = False

    def tick(self, now: float, ph: str, held: bool = False, busy: bool = False) -> str:
        """The receiver is *ph* at *now*; *held*: the person paused it (or an
        Apple TV is still to be picked); *busy*: a stream is on its way to
        the receiver. What happened: "" or one of BEGAN, RESOLVED, RECOVER,
        RECOVERED, FAIL."""
        if self.failed:
            return ""
        if ph == BUFFERING:
            self.buffered_at = now
        if busy:
            self.started = False                # a new stream on its way
        if ph == PLAYING:
            self.started = True
            said = ""
            if self.since is not None and not self.tries:
                self.last_stall = max(0.0, now - self.since)
                said = RESOLVED
            self.since = None
            if self.playing_since is None:
                self.playing_since = now
            if self.tries and now - self.playing_since >= self.settled_s:
                self.tries = 0
                said = RECOVERED
            return said
        self.playing_since = None
        if held or busy or ph in (PAUSED, WAITING, ENDED):
            self.since = None
            return ""
        # After a try, a receiver that is not playing again is still stuck,
        # whatever it says meanwhile.
        stuck = ph in (BUFFERING, ERROR) or (self.tries > 0 and ph in (IDLE, STARTING))
        if not stuck:
            self.since = None
            return ""
        if self.since is None:
            self.since = now
            return BEGAN
        if now - self.since < (self.stall_s if self.started else self.start_stall_s):
            return ""
        self.last_stall = now - self.since
        self.since = None
        if self.tries >= self.tries_max:
            self.failed = True
            return FAIL
        self.tries += 1
        return RECOVER

    def buffering_shown(self, now: float) -> bool:
        """Whether the card says Buffering: the receiver said so within the
        last SHOW_BUFFERING_S (a short wait is seen, not a flicker)."""
        return self.buffered_at is not None and now - self.buffered_at < SHOW_BUFFERING_S

    def recovering(self) -> bool:
        """A try was sent for the stall now and it does not play yet."""
        return self.tries > 0 and self.playing_since is None and not self.failed
