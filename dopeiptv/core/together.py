"""Watch together: the same film, episode or channel with friends, each
playing it from their own source, in step - through a relay on
dopeiptv.org that only passes sealed messages between them.

The room code. Ten characters from an alphabet with nothing to mistake
(no 0/O, no 1/I/L), about 50 bits, shown as two groups of five:
``K7PQM-2XDH9`` (a code of eight from 1.6.7, ``K7PQ-M2XD``, still joins its
room, with the rounds it had). Whoever has it is in the room. Whoever
sees a room id could try codes against it, so each try costs 600 000
rounds of PBKDF2 (``STRETCH``) - with 50 bits, centuries of a graphics
card for one room, which the relay keeps in memory only while it is used.
From it come - stretched, then HKDF-SHA256
as core/sync.py derives its key - the *room id* the relay keeps messages
under (64 hex) and the *key* every message is sealed with
(ChaCha20-Poly1305, core/chacha20poly1305.py). The relay sees the room id,
the sizes (padded to a few steps) and when; never the code, the key, the
names or what plays (donate/together.py on the website branch).

Messages. Who is here (a display name chosen for the session: "hi" on
joining and in answer to one, "ka" now and then, "bye" on leaving) and
what plays: a *state* - what (a Media), playing or paused, where, and the
moment that was true on the relay's clock. A state carries a version; the
highest (version, moment, member) wins everywhere, so the room agrees on
one timeline whoever pressed what.

The clock. Every answer from the relay says its time; the round trips
give each computer the relay's clock (the sample with the shortest trip
wins), so a position sent from one computer lines up on another.

Keeping in step (Corrector): within IN_SYNC nothing happens; a little
ahead, the player holds (pauses) until the others catch up; behind, or
far off, it seeks - a little ahead, by what seeks have been seen to take,
and holds the rest. Never twice in a few seconds, never on a single
reading. A live channel cannot be in step across providers (each has its
own delay): only the channel is shared.

Who said it. Everyone in the room holds the room's key, so the key says
only "someone in the room". Each member therefore makes an Ed25519 key
pair for the room (core/ed25519.py) and its id is the first 20 bytes of
SHA-256 of a label and the public key (member_of) - 40 hex, where an app
up to 1.6.8 has 16 random hex. A message from a 40-hex id carries the public key ("pk") and, as the
last field of its JSON, a signature ("sg") over the room id and the exact
bytes before it; one whose key does not hash to its id or whose signature
does not hold is dropped, so no member can speak as another. A 16-hex id
is taken as before - an older app's, which ignores the two fields it does
not know and so follows a newer one's messages too. (Such an older member
can still be spoken for by another member: updating ends that.) Signing
takes about 5 ms and checking about 3.5 ms of plain Python - every
message is signed, on the link's threads, never the window's.

What is not taken (Room.receive, and Link for the backlog). The relay, or
anyone who learnt the room id, can hand an old sealed message again. A
counter per sender ("n") refuses it while this computer remembers the
sender; after a rejoin or a restart nothing is remembered, so: what the
relay already held when this computer's first request was answered is not
acted on (those in the room answer the hello with what plays now; the
hello waits for that first answer, and where it could not, the backlog is
read under the rules below instead); and, once the relay's clock is known
here, a message is dropped when its moment "t" is more than JOIN_SLACK_MS
before this computer joined, more than STALE_MS old, or more than AHEAD_MS
in the future. A message is stamped as it is sent and normally arrives
within a second - a slow network, three tries at sending and the long
poll's 25 s stay far inside these minutes. Senders are forgotten only
after STALE_MS + AHEAD_MS without a word, when nothing of theirs would be
taken any more. After a longer loss of the relay the window says hello
again, so what was missed comes back in the answers.

Bounds on what another member can cause: at most MEMBERS_MAX people and
SENDERS_MAX senders remembered, a hello answered once every few seconds
per member, a state's version at most VER_JUMP above the room's, a "st"
only for the sender's own choice, positions finite and never sought past
a title's end, a frame exactly one of the padded sizes with nothing but
zeros after its text, at most BATCH_MAX messages of one answer read.

Plain data and the network only: nothing here touches a widget. The
relay is spoken to on the link's own threads; the window (ui/mw_together.py)
keeps the Room on its own thread and hands what it decides to the player.
"""
from __future__ import annotations

import base64
import dataclasses
import functools
import hashlib
import json
import math
import queue
import secrets
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from . import build_check
from . import chacha20poly1305 as aead
from . import ed25519
from .log import log, register_secrets
from .sync import hkdf

ENDPOINT = "https://dopeiptv.org/together/"
MEMBER_HEADER = "X-Together-Member"
#: As the relay counts: more than this many in a room are turned away.
MAX_MEMBERS = 8
#: The longest the relay keeps a request waiting for news (seconds).
WAIT = 25.0

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
#: A new room's code: 10 of the 31 characters, about 50 bits - read aloud in
#: two groups of five. A code of 8 (from 1.6.7) still joins its room.
CODE_LEN = 10
OLD_LEN = 8
_SALT = b"dopeIPTV together v1"
_AAD = b"dopeIPTV together v1"
#: PBKDF2 rounds before HKDF: the code is short, and the relay knows the
#: room id - each guess at a code costs it this much more. A code of the
#: old length keeps its old rounds, so its room is the same room.
STRETCH = 600_000
OLD_STRETCH = 120_000
FORMAT = 1
#: A message's plain text is padded up to one of these, so the sizes the
#: relay sees say little about what was sent.
_PAD = (192, 384, 768, 1536)
#: The most the relay takes in one message (format byte, nonce, text, tag).
MAX_FRAME = 1 + 12 + _PAD[-1] + 16
MIN_FRAME = 1 + 12 + 16

KINDS = ("hi", "ka", "bye", "st")
MEDIA_KINDS = ("movie", "episode", "live")
#: What a member says of themselves: following along, the title is not in
#: their source, nothing playing, still looking.
STATUSES = ("ok", "miss", "idle", "load")
NAME_MAX = 24
TEXT_MAX = 120

#: A member's id: 16 hex from an app up to 1.6.8 (random, bound to nothing),
#: 40 hex from one with a key of its own (the hash of its public key).
OLD_ID_LEN = 16
ID_LEN = 40
_ID_CTX = b"dopeIPTV together member v1"
_SIGN_CTX = b"dopeIPTV together signed v1"
_SIG_HEAD = b',"sg":"'
_SIG_TAIL = len(_SIG_HEAD) + 88 + 2          # ,"sg":"<64 bytes as base64>"}

#: Not taken: a message from more than this before this computer joined...
JOIN_SLACK_MS = 30_000
#: ...older than this against the relay's clock...
STALE_MS = 300_000
#: ...or from further in the future than this.
AHEAD_MS = 300_000
#: The most people a room shows (the relay seats MAX_MEMBERS; one who left
#: without a word is still listed for a while) and senders it remembers.
MEMBERS_MAX = 2 * MAX_MEMBERS
SENDERS_MAX = 64
#: A state whose version is further above the room's than this is not one
#: anybody reached by choosing: nobody could ever choose past it.
VER_JUMP = 10_000
#: A member's hello is answered at most this often (seconds).
HELLO_EVERY = 3.0
#: As many messages as the relay keeps for a room: more in one answer are
#: not read.
BATCH_MAX = 300


# -- the code, the room id and the key ------------------------------------------------

def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    return format_code(raw)


def format_code(raw: str) -> str:
    half = len(raw) // 2
    return f"{raw[:half]}-{raw[half:]}"


def parse_code(text: str) -> str | None:
    """A code as typed or pasted (any case, any separators) in its shown
    form, or None for anything that is not one."""
    s = "".join(ch for ch in str(text or "").upper() if ch.isalnum())
    if len(s) not in (CODE_LEN, OLD_LEN) or any(ch not in ALPHABET for ch in s):
        return None
    return format_code(s)


def is_old_code(code: str) -> bool:
    """Whether *code* is one of the short ones 1.6.7 made (about 40 bits,
    OLD_STRETCH rounds): still joined, never made, and said to be weaker."""
    shown = parse_code(code)
    return shown is not None and len(shown.replace("-", "")) == OLD_LEN


@functools.lru_cache(maxsize=8)
def derive(code: str) -> tuple[str, bytes]:
    """(room id, key) for a code: the name the relay keeps the room's
    messages under (64 hex) and the key they are sealed with, each from its
    own HKDF label - one says nothing about the other."""
    shown = parse_code(code)
    if shown is None:
        raise ValueError("not a room code")
    raw = shown.replace("-", "")
    # Whoever has the code is in the room: never in a log.
    register_secrets(shown, raw, shown.lower())
    rounds = OLD_STRETCH if len(raw) == OLD_LEN else STRETCH
    seed = hashlib.pbkdf2_hmac("sha256", raw.encode("ascii"), _SALT, rounds, 32)
    return hkdf(seed, _SALT, b"room", 32).hex(), hkdf(seed, _SALT, b"key", 32)


@dataclass(frozen=True)
class Identity:
    """This computer in one room: an Ed25519 key pair made for it, and the
    id the others know it by - the hash of the public key, so only the
    holder of *seed* can speak under it."""
    seed: bytes
    public: bytes
    id: str

    def __repr__(self) -> str:
        return f"Identity({self.id})"


def member_of(public: bytes) -> str:
    """The id of the member whose public key is *public* (ID_LEN hex)."""
    return hashlib.sha256(_ID_CTX + public).hexdigest()[:ID_LEN]


def new_identity() -> Identity:
    """This computer's key and id in a room, new for every room it joins."""
    seed = secrets.token_bytes(32)
    public = ed25519.public_key(seed)
    return Identity(seed, public, member_of(public))


# -- a message, sealed ----------------------------------------------------------------

def _signed(room: str, body: bytes) -> bytes:
    return _SIGN_CTX + bytes.fromhex(room) + body


def encode(msg: dict[str, Any], room: str = "", identity: Identity | None = None) -> bytes:
    """*msg* as JSON. With *identity* (whose id must be the message's
    sender) the public key is added and, as the last field, the signature
    over the room and every byte before it - checked on exactly those
    bytes (_signature_holds), so nothing depends on two computers writing
    JSON alike."""
    if identity is None:
        return json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if msg.get("f") != identity.id:
        raise ValueError("not this member's message")
    signed = {k: v for k, v in msg.items() if k not in ("pk", "sg")}
    signed["pk"] = base64.b64encode(identity.public).decode("ascii")
    body = json.dumps(signed, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = base64.b64encode(ed25519.sign(identity.seed, _signed(room, body)))
    return body[:-1] + _SIG_HEAD + sig + b'"}'


def _signature_holds(room: str, text: bytes, d: dict[str, Any], sender: str) -> bool:
    """Whether *text* (the JSON *d* was read from) is signed by the key
    its sender's id is the hash of."""
    tail = text[-_SIG_TAIL:]
    if len(text) <= _SIG_TAIL or not tail.startswith(_SIG_HEAD) or not tail.endswith(b'"}'):
        return False
    sig_text = tail[len(_SIG_HEAD):-2]
    pk_text = d.get("pk")
    if (not isinstance(pk_text, str) or len(pk_text) != 44
            or d.get("sg") != sig_text.decode("ascii", "replace")):
        return False
    try:
        public = base64.b64decode(pk_text, validate=True)
        sig = base64.b64decode(sig_text, validate=True)
    except ValueError:
        return False
    if len(public) != 32 or len(sig) != 64 or member_of(public) != sender:
        return False
    return ed25519.verify(public, _signed(room, text[:-_SIG_TAIL] + b"}"), sig)


def seal(key: bytes, room: str, msg: dict[str, Any], identity: Identity | None = None) -> bytes:
    """*msg* as the relay passes it: a format byte, a random nonce, and the
    padded JSON (signed, with *identity*), encrypted and tied to the room."""
    return _seal_text(key, room, encode(msg, room, identity))


def _seal_text(key: bytes, room: str, text: bytes) -> bytes:
    size = next((p for p in _PAD if p >= len(text) + 2), 0)
    if not size:
        raise ValueError("message too long")
    plain = struct.pack(">H", len(text)) + text + b"\0" * (size - 2 - len(text))
    nonce = secrets.token_bytes(12)
    head = bytes([FORMAT])
    return head + nonce + aead.encrypt(key, nonce, plain, _AAD + head + bytes.fromhex(room))


def _open_text(key: bytes, room: str, blob: Any) -> bytes | None:
    """The JSON text sealed in *blob*, or None: another key, damaged, a
    newer format - or not exactly as seal() makes one (a size that is none
    of _PAD, anything but zeros after the text): nothing rides along."""
    if not isinstance(blob, (bytes, bytearray)) or not MIN_FRAME <= len(blob) <= MAX_FRAME:
        return None
    if blob[0] != FORMAT:
        return None
    try:
        plain = aead.decrypt(key, bytes(blob[1:13]), bytes(blob[13:]),
                             _AAD + bytes(blob[:1]) + bytes.fromhex(room))
    except ValueError:
        return None
    if len(plain) not in _PAD:
        return None
    (length,) = struct.unpack(">H", plain[:2])
    if length > len(plain) - 2 or any(plain[2 + length:]):
        return None
    return plain[2:2 + length]


def _loads(text: bytes | None) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        msg = json.loads(text.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    return msg if isinstance(msg, dict) else None


def open_frame(key: bytes, room: str, blob: bytes) -> dict[str, Any] | None:
    """The message in *blob* as sent, unchecked (open_message checks who
    sent it), or None for anything that is not one of this room's."""
    return _loads(_open_text(key, room, blob))


def open_message(key: bytes, room: str, blob: bytes) -> Message | None:
    """The message in *blob*, well formed and from who it says: under a
    key-bound id (ID_LEN hex) only with that key's signature; under an
    older app's id as it is. None for anything else."""
    text = _open_text(key, room, blob)
    d = _loads(text)
    msg = parse(d)
    if msg is None or text is None or d is None:
        return None
    if len(msg.sender) != ID_LEN:
        return msg
    if not _signature_holds(room, text, d, msg.sender):
        log.debug("together: a message not signed by its sender was dropped")
        return None
    return dataclasses.replace(msg, signed=True)


# -- what is said -------------------------------------------------------------------

def _clean(text: Any, most: int) -> str:
    """Text from another computer as this one shows it: one line, no
    control characters, nothing a label would take for markup."""
    s = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    s = s.replace("<", "‹").replace(">", "›")
    return " ".join(s.split())[:most]


def clean_name(text: Any) -> str:
    return _clean(text, NAME_MAX)


def _int(value: Any, lo: int = 0, hi: int = 1 << 53) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        n = int(value)
    except (ValueError, OverflowError):
        return None
    return n if lo <= n <= hi else None


def _member_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) in (OLD_ID_LEN, ID_LEN) and all(ch in "0123456789abcdef" for ch in value):
        return value
    return None


@dataclass(frozen=True)
class Media:
    """What plays, as two sources would both know it. *name* is what people
    read; *title* (cleaned) and *year*, or *tmdb*, find a film or series;
    *season* and *episode* the episode; *guide* (the guide id) or the name
    a live channel."""
    kind: str
    name: str
    title: str = ""
    year: int | None = None
    tmdb: int | None = None
    season: int | None = None
    episode: int | None = None
    guide: str = ""

    @property
    def synced(self) -> bool:
        """Whether positions are kept in step (not for a live channel)."""
        return self.kind != "live"

    def wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"k": self.kind, "n": self.name}
        for key, value in (("q", self.title), ("y", self.year), ("tm", self.tmdb),
                           ("s", self.season), ("e", self.episode), ("g", self.guide)):
            if value not in (None, ""):
                out[key] = value
        return out

    @classmethod
    def from_wire(cls, d: Any) -> Media | None:
        if not isinstance(d, dict) or d.get("k") not in MEDIA_KINDS:
            return None
        name = _clean(d.get("n"), TEXT_MAX)
        if not name:
            return None
        return cls(kind=str(d["k"]), name=name, title=_clean(d.get("q"), TEXT_MAX),
                   year=_int(d.get("y"), 1870, 2200), tmdb=_int(d.get("tm"), 1),
                   season=_int(d.get("s"), 0, 9999), episode=_int(d.get("e"), 0, 99999),
                   guide=_clean(d.get("g"), TEXT_MAX))


@dataclass(frozen=True)
class PlayState:
    """The room's timeline: *media* at *pos* seconds at the relay moment *t*
    (ms), playing or paused. (*ver*, *t*, *by*) orders two states."""
    media: Media
    playing: bool
    pos: float
    t: int
    ver: int
    by: str

    def position_at(self, now_ms: int) -> float:
        if not self.playing:
            return self.pos
        return max(0.0, self.pos + max(0, now_ms - self.t) / 1000.0)

    def newer_than(self, other: PlayState | None) -> bool:
        if other is None:
            return True
        return (self.ver, self.t, self.by) > (other.ver, other.t, other.by)

    def wire(self) -> dict[str, Any]:
        return {"m": self.media.wire(), "p": self.playing, "x": round(self.pos, 3),
                "t": self.t, "v": self.ver, "b": self.by}

    @classmethod
    def from_wire(cls, d: Any) -> PlayState | None:
        if not isinstance(d, dict):
            return None
        media = Media.from_wire(d.get("m"))
        pos = d.get("x")
        t, ver, by = _int(d.get("t"), 1), _int(d.get("v"), 1), _member_id(d.get("b"))
        if (media is None or t is None or ver is None or by is None
                or isinstance(pos, bool) or not isinstance(pos, (int, float))
                or not math.isfinite(pos) or not 0 <= pos < 1e7
                or not isinstance(d.get("p"), bool)):
            return None
        return cls(media, bool(d["p"]), float(pos), t, ver, by)


@dataclass(frozen=True)
class Message:
    kind: str
    sender: str
    n: int
    t: int
    name: str = ""
    answer: bool = False
    status: str = ""
    state: PlayState | None = None
    #: Proven to be its sender's (open_message); an older app's never is.
    signed: bool = False


def parse(d: Any) -> Message | None:
    """A message as opened (open_frame), checked field by field; None for
    one that is not well formed."""
    if not isinstance(d, dict) or d.get("k") not in KINDS:
        return None
    sender, n, t = _member_id(d.get("f")), _int(d.get("n"), 1), _int(d.get("t"), 1)
    if sender is None or n is None or t is None:
        return None
    state = None
    if "st" in d:
        state = PlayState.from_wire(d.get("st"))
        if state is None:
            return None
    status = str(d.get("s") or "")
    return Message(kind=str(d["k"]), sender=sender, n=n, t=t, name=clean_name(d.get("name")),
                   answer=d.get("re") is True, status=status if status in STATUSES else "",
                   state=state)


# -- the relay's clock ------------------------------------------------------------------

class Clock:
    """The relay's time on this computer: an offset to the monotonic clock,
    from the round trip with the shortest time among the last few. Until
    one is known, this computer's own wall clock stands in. Thread-safe:
    the link's threads add samples, the window reads."""

    KEEP = 16
    #: A round trip longer than this says too little to be a sample.
    MAX_RTT = 4.0

    def __init__(self, mono: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self._mono = mono
        self._lock = threading.Lock()
        self._samples: deque[tuple[float, float]] = deque(maxlen=self.KEEP)
        self._fallback = wall() - mono()

    def sample(self, sent: float, server_ms: int, received: float) -> None:
        """A request sent at *sent* and answered at *received* (this
        computer's monotonic seconds), the relay saying *server_ms*."""
        rtt = received - sent
        if not 0 <= rtt <= self.MAX_RTT or server_ms <= 0:
            return
        offset = server_ms / 1000.0 - (sent + received) / 2.0
        with self._lock:
            self._samples.append((rtt, offset))

    @property
    def known(self) -> bool:
        with self._lock:
            return bool(self._samples)

    def offset(self) -> float:
        with self._lock:
            if not self._samples:
                return self._fallback
            return min(self._samples)[1]

    def mono(self) -> float:
        """This computer's monotonic seconds, as now_ms() takes them."""
        return self._mono()

    def uncertainty(self) -> float:
        """Half the best round trip: how far off the clock may be (s)."""
        with self._lock:
            return min(self._samples)[0] / 2.0 if self._samples else 1.0

    def now_ms(self, mono: float | None = None) -> int:
        at = self._mono() if mono is None else mono
        return int(round((at + self.offset()) * 1000.0))


# -- the room ----------------------------------------------------------------------------

@dataclass
class Member:
    id: str
    name: str
    seen: float
    status: str = ""
    greeted: float = float("-inf")


class Room:
    """Who is in the room and what it plays, as this computer knows it.
    The window's thread only: messages in (receive), messages out (hello,
    keepalive, bye, publish), members gone quiet (expire)."""

    KEEPALIVE = 60.0
    #: A member silent for this long has gone (a closed laptop says no bye).
    GONE_AFTER = 150.0
    #: A state from further in the future than this is taken as now.
    FUTURE_MS = 5000

    def __init__(self, code: str, me: str, name: str, clock: Clock) -> None:
        self.code = code
        self.me = me
        self.name = clean_name(name) or "?"
        self.clock = clock
        self.members: dict[str, Member] = {}
        self.state: PlayState | None = None
        self.status = "idle"
        self._sent = 0
        self._born = clock.mono()
        #: Per sender: the highest "n" seen and when (this computer's seconds).
        self._last_n: dict[str, tuple[int, float]] = {}

    # -- out
    def _make(self, kind: str, **fields: Any) -> dict[str, Any]:
        self._sent += 1
        msg: dict[str, Any] = {"k": kind, "f": self.me, "n": self._sent, "t": self.clock.now_ms()}
        msg.update({k: v for k, v in fields.items() if v is not None})
        return msg

    def hello(self, answer: bool = False) -> dict[str, Any]:
        return self._make("hi", name=self.name, re=answer or None, s=self.status,
                          st=self.state.wire() if self.state is not None else None)

    def keepalive(self) -> dict[str, Any]:
        return self._make("ka", name=self.name, s=self.status)

    def bye(self) -> dict[str, Any]:
        return self._make("bye")

    def publish(self, media: Media, playing: bool, pos: float) -> dict[str, Any]:
        """This computer chose: *media* at *pos*, playing or not. The new
        state is the room's at once, and the message to send it."""
        ver = (self.state.ver if self.state is not None else 0) + 1
        self.state = PlayState(media, bool(playing), max(0.0, float(pos)),
                               self.clock.now_ms(), ver, self.me)
        return self._make("st", st=self.state.wire())

    # -- in
    def _timely(self, msg: Message) -> bool:
        """Whether *msg* is from this stay in the room and from about now
        (the module's docstring says why each bound). Without the relay's
        clock nothing can be said, and nothing is refused."""
        if not self.clock.known:
            return True
        relay_now = self.clock.now_ms()
        if not relay_now - STALE_MS <= msg.t <= relay_now + AHEAD_MS:
            return False
        return msg.t >= self.clock.now_ms(self._born) - JOIN_SLACK_MS

    def _counted(self, msg: Message, now: float) -> bool:
        """Whether *msg* is new from its sender - and its number kept."""
        last = self._last_n.get(msg.sender)
        if last is not None and msg.n <= last[0]:
            return False
        if last is None and len(self._last_n) >= SENDERS_MAX:
            keep = (STALE_MS + AHEAD_MS) / 1000.0
            for sender in [s for s, (_n, at) in self._last_n.items()
                           if now - at > keep and s not in self.members]:
                del self._last_n[sender]
            if len(self._last_n) >= SENDERS_MAX:
                return False
        self._last_n[msg.sender] = (msg.n, now)
        return True

    def receive(self, msg: Message, now: float) -> list[tuple[str, Any]]:
        """What *msg* changes, as events: ("joined" | "left" | "status" |
        "hello", Member), ("state", (old, new)). Nothing for one of this
        computer's own, an old one handed again or one out of its time."""
        if msg.sender == self.me:
            return []
        if not self._timely(msg):
            log.debug("together: a message out of its time was ignored")
            return []
        if not self._counted(msg, now):
            return []
        events: list[tuple[str, Any]] = []
        if msg.kind == "bye":
            gone = self.members.pop(msg.sender, None)
            return [("left", gone)] if gone is not None else []
        relay_now = self.clock.now_ms()
        age = max(0.0, (relay_now - msg.t) / 1000.0)
        member = self.members.get(msg.sender)
        if age <= self.GONE_AFTER:
            seen = now - age
            if member is None and msg.kind not in ("hi", "ka"):
                pass            # known by name once they say hello (they answer ours)
            elif member is None:
                if len(self.members) < MEMBERS_MAX:
                    member = Member(msg.sender, msg.name or "?", seen, msg.status)
                    self.members[msg.sender] = member
                    events.append(("joined", member))
            else:
                member.seen = max(member.seen, seen)
                if msg.name:
                    member.name = msg.name
                if msg.status and msg.status != member.status:
                    member.status = msg.status
                    events.append(("status", member))
        state = msg.state
        if state is not None and msg.kind == "st" and state.by != msg.sender:
            state = None        # a choice is told by whoever made it
        if (state is not None and self.state is not None
                and state.ver > self.state.ver + VER_JUMP):
            state = None
        if state is not None and state.t > relay_now + self.FUTURE_MS:
            state = PlayState(state.media, state.playing, state.pos, relay_now,
                              state.ver, state.by)
        if state is not None and state.newer_than(self.state):
            old, self.state = self.state, state
            events.append(("state", (old, state)))
        if (msg.kind == "hi" and not msg.answer and member is not None
                and now - member.greeted >= HELLO_EVERY):
            member.greeted = now
            events.append(("hello", member))
        return events

    def expire(self, now: float) -> list[Member]:
        gone = [m for m in self.members.values() if now - m.seen > self.GONE_AFTER]
        for m in gone:
            del self.members[m.id]
        return gone

    def names(self) -> list[str]:
        return sorted((m.name for m in self.members.values()), key=str.casefold)


# -- keeping in step -----------------------------------------------------------------------

@dataclass(frozen=True)
class Fix:
    """What the player should do: "none", "play", "pause", "seek" (to
    *value* s) or "hold" (pause for *value* s, then play)."""
    action: str
    value: float = 0.0


NONE = Fix("none")


class Corrector:
    """Decides, once a tick, how this player follows the room's timeline -
    gently: nothing within IN_SYNC, a hold when a little ahead, a seek only
    beyond that, and never on one reading or within GRACE of the last."""

    IN_SYNC = 1.0
    #: Paused, positions this close are the same frame for anyone watching.
    PAUSED_TOLERANCE = 0.5
    #: Ahead by up to this, wait for the others rather than seek back.
    HOLD_MAX = 6.0
    GRACE = 5.0
    STRIKES = 2
    LEAD_START = 1.5
    LEAD_MIN, LEAD_MAX = 0.3, 6.0
    #: The last seconds of a title: nothing to catch up to.
    END_SLACK = 3.0

    def __init__(self) -> None:
        self.lead = self.LEAD_START
        self._strikes = 0
        self._quiet_until = 0.0
        self._sought: tuple[float, float] | None = None     # (lead used, when)

    def reset(self) -> None:
        self._strikes = 0
        self._quiet_until = 0.0
        self._sought = None

    def decide(self, pos: float, playing: bool, want: float, want_playing: bool,
               now: float, duration: float = 0.0) -> Fix:
        if want_playing != playing:
            self._strikes = 0
            # Paused here and there: the next tick lines the frames up.
            self._quiet_until = now + (0.9 if not want_playing else 2.0)
            return Fix("play" if want_playing else "pause")
        if now < self._quiet_until:
            return NONE
        drift = pos - want
        if self._sought is not None:
            used, _when = self._sought
            self._sought = None
            if playing:
                # Landed *drift* off: a lead of (used - drift) would have hit.
                self.lead = min(self.LEAD_MAX, max(self.LEAD_MIN, 0.5 * self.lead + 0.5 * (used - drift)))
        if not want_playing:
            self._strikes = 0
            if duration > 0:
                # Never past the title's end, wherever the room says it is.
                want = min(want, max(0.0, duration - self.PAUSED_TOLERANCE))
                drift = pos - want
            if abs(drift) > self.PAUSED_TOLERANCE:
                self._quiet_until = now + 2.0
                return Fix("seek", max(0.0, want))
            return NONE
        if abs(drift) <= self.IN_SYNC:
            self._strikes = 0
            return NONE
        if duration > 0 and want >= duration - self.END_SLACK:
            return NONE
        self._strikes += 1
        if self._strikes < self.STRIKES:
            return NONE
        self._strikes = 0
        if 0 < drift <= self.HOLD_MAX:
            self._quiet_until = now + drift + self.GRACE
            return Fix("hold", drift)
        target = want + self.lead
        if duration > 0:
            target = min(target, max(0.0, duration - self.END_SLACK))
        self._sought = (self.lead, now)
        self._quiet_until = now + self.GRACE
        return Fix("seek", max(0.0, target))


class LocalWatch:
    """Tells a seek made here (the slider, a key) from playback running on
    or stalling: a jump forward past what the time allows, or back. The
    player's own corrections are announced first (expect_seek)."""

    JUMP = 2.0
    #: An announced seek counts as ours if it lands this close, this soon.
    NEAR = 3.0
    EXPECT_FOR = 10.0

    def __init__(self) -> None:
        self._last: tuple[float, float, bool] | None = None
        self._expect: tuple[float, float] | None = None

    def reset(self) -> None:
        self._last = None
        self._expect = None

    def expect_seek(self, target: float, now: float) -> None:
        self._expect = (target, now + self.EXPECT_FOR)

    def tick(self, pos: float, playing: bool, now: float) -> bool:
        """True when *pos* says someone here moved the position."""
        last, self._last = self._last, (pos, now, playing)
        if last is None:
            return False
        lpos, lnow, lplaying = last
        allowed = (now - lnow) if lplaying else 0.0
        jumped = pos > lpos + allowed + self.JUMP or pos < lpos - self.JUMP
        if not jumped:
            return False
        expect = self._expect
        if expect is not None and now <= expect[1] and abs(pos - expect[0]) <= self.NEAR:
            self._expect = None
            return False
        return True


# -- the relay ----------------------------------------------------------------------------

class RelayError(Exception):
    """A request the relay did not answer as hoped. *kind*: net (no answer),
    busy (too many rooms, or too many requests), full (the room has
    MAX_MEMBERS already), bad (refused as malformed), modified (not asked:
    a modified build)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass
class Polled:
    seq: int
    msgs: list[tuple[int, bytes]]
    now: int
    members: int
    reset: bool = False


class Relay:
    """POST, GET (a long poll) and DELETE of one room on dopeiptv.org.
    Blocking: the link's threads'. One per thread (each has a session)."""

    def __init__(self, room: str, member: str, endpoint: str | None = None) -> None:
        self.url = (endpoint or ENDPOINT) + room
        from .. import __version__
        self.headers = {MEMBER_HEADER: member, "User-Agent": f"dopeIPTV/{__version__}",
                        **build_check.headers()}
        self._session: Any = None

    def _http(self) -> Any:
        if self._session is None:
            from ._lazy_requests import requests
            self._session = requests.Session()
        return self._session

    def _call(self, method: str, timeout: float, headers: dict[str, str] | None = None,
              **kw: Any) -> Any:
        if not build_check.allowed():
            # Never asked from a modified build: the relay refuses it anyway.
            raise RelayError("modified")
        if not build_check.secure(self.url):
            log.warning("together: not https - nothing sent")
            raise RelayError("net")
        try:
            r = self._http().request(method, self.url, headers=headers or self.headers,
                                     timeout=(6.0, timeout), allow_redirects=False, **kw)
        except Exception as e:
            log.info("together: %s not answered (%s)", method, type(e).__name__)
            raise RelayError("net") from e
        if r.status_code == 200:
            try:
                got = r.json()
            except ValueError as e:
                raise RelayError("net") from e
            if isinstance(got, dict):
                return got
            raise RelayError("net")
        if r.status_code == 403:
            raise RelayError("full")
        if r.status_code in (429, 502, 503, 504):
            raise RelayError("busy")
        if r.status_code in (400, 404, 413):
            raise RelayError("bad")
        raise RelayError("net")

    def post(self, blob: bytes) -> int:
        """Sends one sealed message; the relay's time (ms)."""
        got = self._call("POST", 15.0, data=bytes(blob),
                         headers=dict(self.headers, **{"Content-Type": "application/octet-stream"}))
        return _int(got.get("now"), 0) or 0

    def poll(self, after: int, wait: float) -> Polled:
        got = self._call("GET", wait + 15.0, params={"after": max(0, int(after)),
                                                     "wait": f"{max(0.0, min(WAIT, wait)):g}"})
        msgs: list[tuple[int, bytes]] = []
        for row in got.get("msgs") or []:
            try:
                seq, text = int(row[0]), str(row[1])
                msgs.append((seq, base64.b64decode(text, validate=True)))
            except (TypeError, ValueError, IndexError):
                continue
        return Polled(seq=_int(got.get("seq"), 0) or 0, msgs=msgs,
                      now=_int(got.get("now"), 0) or 0,
                      members=_int(got.get("n"), 0, 1000) or 0, reset=got.get("reset") is True)

    def leave(self) -> None:
        try:
            self._call("DELETE", 5.0)
        except RelayError:
            pass

    def close(self) -> None:
        s, self._session = self._session, None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


class Link:
    """A room's connection: one thread long-polls the relay and opens what
    comes, one seals and sends. *on_message* gets each Message, *on_status*
    (kind, members) how the connection goes - "ok", "net", "busy", "full";
    both on the link's threads. *relay_for(room, member)* makes a relay
    for a thread (tests hand in one of their own). With *identity* (whose
    id is *me*) everything sent is signed - on the sending thread: a
    signature takes a few milliseconds of plain Python.

    What the relay held before this link's first request was answered is
    not delivered (an old room's messages, or old ones handed again), and
    nothing is sent before that answer, so every answer to this
    computer's hello comes after it. Should that first answer not come
    within FIRST_POLL_WAIT the sending goes ahead, and the backlog is then
    delivered - the room's own rules of time judge it."""

    BACKOFF = (2.0, 5.0, 10.0, 20.0, 30.0)
    SEND_TRIES = 3
    FIRST_POLL_WAIT = 8.0

    def __init__(self, room: str, key: bytes, me: str, clock: Clock,
                 on_message: Callable[[Message], None],
                 on_status: Callable[[str, int], None],
                 relay_for: Callable[[str, str], Any] | None = None,
                 endpoint: str | None = None, identity: Identity | None = None) -> None:
        if identity is not None and identity.id != me:
            raise ValueError("the identity is another member's")
        self.room, self.key, self.me, self.clock = room, key, me, clock
        self.identity = identity
        self._polled = threading.Event()
        self._posted = threading.Event()
        self._on_message, self._on_status = on_message, on_status
        self._relay_for = relay_for or functools.partial(Relay, endpoint=endpoint)
        self._stop = threading.Event()
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=64)
        self._status: tuple[str, int] | None = None
        self._threads: list[threading.Thread] = []
        self._poll_relay: Any = None

    def start(self) -> None:
        for target, name in ((self._poll_loop, "together-poll"), (self._send_loop, "together-send")):
            t = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(t)
            t.start()

    def send(self, msg: dict[str, Any]) -> None:
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            log.info("together: a message was not sent (too many waiting)")

    def close(self, bye: dict[str, Any] | None = None, wait: float = 0.0) -> None:
        """Stop; *bye* goes first, then the seat is given up. *wait*: how
        long to wait for that (the window closing waits a moment)."""
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            if bye is not None:
                self._queue.put_nowait(bye)
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        relay = self._poll_relay
        if relay is not None and hasattr(relay, "close"):
            relay.close()
        if wait > 0 and len(self._threads) > 1:
            self._threads[1].join(wait)

    @property
    def closed(self) -> bool:
        return self._stop.is_set()

    def _tell(self, kind: str, members: int = 0) -> None:
        if self._stop.is_set() and kind != "closed":
            return
        if self._status == (kind, members):
            return
        self._status = (kind, members)
        try:
            self._on_status(kind, members)
        except Exception:
            log.debug("together: status not delivered", exc_info=True)

    def _poll_loop(self) -> None:
        relay = self._poll_relay = self._relay_for(self.room, self.me)
        after, first, fails = 0, True, 0
        while not self._stop.is_set():
            sent = time.monotonic()
            try:
                got = relay.poll(after, 0.0 if first else WAIT)
            except RelayError as e:
                if self._stop.is_set():
                    break
                fails += 1
                self._tell(e.kind)
                if e.kind == "modified":
                    break
                self._stop.wait(self.BACKOFF[min(fails, len(self.BACKOFF)) - 1])
                continue
            except Exception:
                log.warning("together: poll failed", exc_info=True)
                self._stop.wait(self.BACKOFF[-1])
                continue
            received = time.monotonic()
            if self._stop.is_set():
                break
            fails = 0
            if first:
                # Only an answer the relay did not hold back times its clock.
                self.clock.sample(sent, got.now, received)
            if got.reset:
                log.info("together: the relay started the room over")
            msgs = got.msgs[-BATCH_MAX:]
            if first and not self._posted.is_set():
                if msgs:
                    log.debug("together: %d messages from before joining left unread", len(msgs))
                msgs = []
            self._polled.set()
            for _seq, blob in msgs:
                if self._stop.is_set():
                    break
                msg = open_message(self.key, self.room, blob)
                if msg is None or msg.sender == self.me:
                    continue
                try:
                    self._on_message(msg)
                except Exception:
                    log.debug("together: message not delivered", exc_info=True)
            after = got.seq
            self._tell("ok", got.members)
            if not first and not got.msgs and received - sent < 1.0:
                self._stop.wait(2.0)        # the relay did not wait: no busy loop
            first = False
        self._polled.set()
        if hasattr(relay, "close"):
            relay.close()

    def _await_first_poll(self) -> None:
        end = time.monotonic() + self.FIRST_POLL_WAIT
        while not (self._polled.is_set() or self._stop.is_set()) and time.monotonic() < end:
            self._polled.wait(0.1)
        self._posted.set()

    def _send_loop(self) -> None:
        relay = self._relay_for(self.room, self.me)
        while True:
            msg = self._queue.get()
            if msg is None:
                break
            if not self._posted.is_set():
                self._await_first_poll()
            if self.clock.known and "t" in msg:
                # The moment it leaves, on the relay's clock: what the
                # others judge its age by (a state's own moment is its own).
                msg = dict(msg, t=self.clock.now_ms())
            try:
                blob = seal(self.key, self.room, msg, self.identity)
            except ValueError:
                log.info("together: a message too long was not sent")
                continue
            for attempt in range(self.SEND_TRIES):
                sent = time.monotonic()
                try:
                    now = relay.post(blob)
                except RelayError as e:
                    if e.kind in ("bad", "full", "modified") or attempt == self.SEND_TRIES - 1:
                        self._tell(e.kind)
                        break
                    time.sleep(1.0 + attempt)
                    continue
                except Exception:
                    log.warning("together: send failed", exc_info=True)
                    break
                self.clock.sample(sent, now, time.monotonic())
                break
        try:
            relay.leave()
        except Exception:
            pass
        if hasattr(relay, "close"):
            relay.close()
