"""Nerd: a simple ETSI TR 101 290 for the live channel that plays.

What the transport stream carries (ISO/IEC 13818-1): the programmes in
its PAT, their PIDs and stream types in the PMT, the service's name from
the SDT, the bitrate of each PID and of the whole over the last seconds,
the share of null packets, which PIDs are scrambled. And the measurement
guidelines' first two priorities, each counted as TR 101 290 names it:

- 1.1 TS_sync_loss, 1.2 Sync_byte_error: the 0x47 every 188 bytes - two
  wrong in a row lose the sync, five right ones find it again;
- 1.3 PAT_error_2, 1.5 PMT_error_2: the tables at least every 0.5 s, with
  the right table id and never scrambled;
- 1.4 Continuity_count_error: each PID's 4-bit counter in order - a
  packet sent twice is allowed once, an adaptation field's
  discontinuity_indicator starts it anew;
- 1.6 PID_error: a picture or sound PID the PMT names and that does not
  come for 5 s;
- 2.1 Transport_error, 2.2 CRC_error (PAT, CAT, PMT, SDT);
- 2.3a PCR_repetition_error (more than 40 ms between PCRs), 2.3b
  PCR_discontinuity_indicator_error (a jump of more than 100 ms, or back,
  without the indicator);
- 2.4 PCR_accuracy_error as an estimate: the real test (500 ns) needs a
  measuring receiver; here each PCR is set against when its packet arrived,
  so the network's own jitter is in it (counted beyond JITTER_LIMIT);
- 2.5 PTS_error (a picture or sound PID's PTS more than 700 ms apart),
  2.6 CAT_error (scrambled packets and no CAT).

Times are the stream's own: its PCR, not the clock on the wall, so a
stream read in a burst or after a wait is judged as it was sent. Fast
enough for 20 Mbit/s and more in plain Python (one pass, a few
attribute lookups per packet), and bounded: at most MAX_PIDS PIDs, small
windows, nothing that grows with the stream's length.

``Analyzer`` is the pure part (``feed`` bytes, ``snapshot`` what it found);
``Runner`` reads a live channel's rewind buffer (core/dvr_store.py) into
one on a thread of its own - never a second connection to the account -
and caps its own work so playback never waits on it.
"""

from __future__ import annotations

import threading
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .log import log

PACKET = 188
SYNC = 0x47
NULL_PID = 0x1FFF
PCR_HZ = 27_000_000
PCR_WRAP = (1 << 33) * 300

#: The guidelines' limits, in seconds of the stream.
PAT_LIMIT = 0.5
PMT_LIMIT = 0.5
PCR_LIMIT = 0.040
PCR_JUMP = 0.100
PTS_LIMIT = 0.700
PID_LIMIT = 5.0
#: Arrival against PCR beyond this counts for 2.4 (an estimate at the
#: network, where tens of milliseconds are usual and a player's buffer
#: takes them; this much shows as stutter with a small buffer).
JITTER_LIMIT = 0.250
JITTER_WINDOW = 10.0
#: How long counts as "now" for the summary, in seconds.
RECENT = 10.0
#: The bitrate's sliding window, in seconds.
WINDOW = 5
#: Seconds of PCR jitter kept for the sparkline.
HISTORY = 120
#: Sync: five right in a row find it, two wrong in a row lose it (TR 101
#: 290, after ISO/IEC 13818-1 G.1).
SYNC_FIND = 5
SYNC_LOSE = 2
#: Bounds.
MAX_PIDS = 256
MAX_PROGRAMS = 64
MAX_SECTION = 4096 + 3
MAX_CACHE = 256
#: Wall-clock seconds without the reference PCR before the clock on the
#: wall takes over the stream's time.
NO_PCR = 3.0
#: Bytes without a sync found from the start: not a transport stream.
NOT_TS_BYTES = 1 << 20
#: The bytes of a chunk are taken to have arrived over at most this long
#: before it was read (the rewind buffer writes them in blocks of 0.1 s).
SPREAD = 0.2

OK, WARN, ERROR, WAITING = "ok", "warn", "error", "waiting"

#: (key, priority, its number in TR 101 290, its name there).
INDICATORS: tuple[tuple[str, int, str, str], ...] = (
    ("sync_loss", 1, "1.1", "TS_sync_loss"),
    ("sync_byte", 1, "1.2", "Sync_byte_error"),
    ("pat", 1, "1.3", "PAT_error_2"),
    ("cc", 1, "1.4", "Continuity_count_error"),
    ("pmt", 1, "1.5", "PMT_error_2"),
    ("pid", 1, "1.6", "PID_error"),
    ("transport", 2, "2.1", "Transport_error"),
    ("crc", 2, "2.2", "CRC_error"),
    ("pcr_rep", 2, "2.3a", "PCR_repetition_error"),
    ("pcr_disc", 2, "2.3b", "PCR_discontinuity_indicator_error"),
    ("pcr_acc", 2, "2.4", "PCR_accuracy_error"),
    ("pts", 2, "2.5", "PTS_error"),
    ("cat", 2, "2.6", "CAT_error"),
)

#: A stream type (and, for 0x06, its descriptors) as a codec and a kind.
STREAM_TYPES: dict[int, tuple[str, str]] = {
    0x01: ("mpeg1v", "video"), 0x02: ("mpeg2v", "video"),
    0x03: ("mp2", "audio"), 0x04: ("mp2", "audio"),
    0x05: ("sections", "data"), 0x06: ("private", "data"),
    0x0A: ("dsmcc", "data"), 0x0B: ("dsmcc", "data"), 0x0C: ("dsmcc", "data"),
    0x0D: ("dsmcc", "data"), 0x0F: ("aac", "audio"), 0x10: ("mpeg4v", "video"),
    0x11: ("aac_latm", "audio"), 0x15: ("id3", "data"), 0x1B: ("h264", "video"),
    0x1C: ("mpeg4a", "audio"), 0x24: ("hevc", "video"), 0x2D: ("mpegh", "audio"),
    0x33: ("vvc", "video"), 0x42: ("avs", "video"), 0x81: ("ac3", "audio"),
    0x82: ("dts", "audio"), 0x86: ("scte35", "data"), 0x87: ("eac3", "audio"),
    0xD1: ("dirac", "video"), 0xEA: ("vc1", "video"),
}

#: Codecs by name, as anyone who knows them writes them.
CODEC_NAMES: dict[str, str] = {
    "mpeg1v": "MPEG-1 video", "mpeg2v": "MPEG-2 video", "mpeg4v": "MPEG-4 video",
    "h264": "H.264", "hevc": "HEVC", "vvc": "H.266/VVC", "avs": "AVS",
    "vc1": "VC-1", "dirac": "Dirac", "mp2": "MP2", "aac": "AAC",
    "aac_latm": "AAC (LATM)", "mpeg4a": "MPEG-4 audio", "mpegh": "MPEG-H",
    "ac3": "AC-3", "eac3": "E-AC-3", "ac4": "AC-4", "dts": "DTS", "opus": "Opus",
    "dvbsub": "DVB subtitles", "teletext": "Teletext", "scte35": "SCTE-35",
    "id3": "ID3", "klv": "KLV", "dsmcc": "DSM-CC", "sections": "sections",
    "private": "private data", "pat": "PAT", "cat": "CAT", "pmt": "PMT",
    "nit": "NIT", "sdt": "SDT/BAT", "eit": "EIT", "tdt": "TDT/TOT",
    "tsdt": "TSDT", "rst": "RST", "null": "null", "unknown": "?",
}

_RESERVED = {0x0000: "pat", 0x0001: "cat", 0x0002: "tsdt", 0x0010: "nit",
             0x0011: "sdt", 0x0012: "eit", 0x0013: "rst", 0x0014: "tdt",
             NULL_PID: "null"}
#: Kinds a PID_error and a PTS_error are looked for on: the others may be
#: silent for long (no subtitles now, no ad break).
_STEADY = ("video", "audio")
_REGISTRATION = {b"AC-3": ("ac3", "audio"), b"EAC3": ("eac3", "audio"),
                 b"AC-4": ("ac4", "audio"), b"HEVC": ("hevc", "video"),
                 b"Opus": ("opus", "audio"), b"CUEI": ("scte35", "data"),
                 b"KLVA": ("klv", "data"), b"DTS1": ("dts", "audio"),
                 b"DTS2": ("dts", "audio"), b"DTS3": ("dts", "audio")}
_DESCRIPTOR = {0x6A: ("ac3", "audio"), 0x7A: ("eac3", "audio"),
               0x7B: ("dts", "audio"), 0x7C: ("aac", "audio"),
               0x59: ("dvbsub", "subtitles"), 0x56: ("teletext", "teletext"),
               0x46: ("teletext", "teletext")}

_REV8 = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def crc32_mpeg(data: bytes) -> int:
    """CRC-32/MPEG-2 (polynomial 0x04C11DB7, not reflected, no final xor):
    0 over a whole section with its CRC when it is intact. zlib's CRC-32
    with the bits of each byte and of the result turned round, done in C."""
    c = zlib.crc32(data.translate(_REV8))
    return int(f"{c:032b}"[::-1], 2) ^ 0xFFFFFFFF


def dvb_text(raw: bytes) -> str:
    """A DVB string (EN 300 468 annex A): its first byte may say the
    character table; the emphasis and control codes go."""
    if not raw:
        return ""
    first = raw[0]
    enc, body = "latin-1", raw
    if first >= 0x20:
        enc = "latin-1"         # ISO/IEC 6937: its letters are latin-1's
    elif 0x01 <= first <= 0x0B:
        enc, body = f"iso8859-{first + 4}", raw[1:]
    elif first == 0x10 and len(raw) >= 3:
        enc, body = f"iso8859-{raw[2]}", raw[3:]
    elif first == 0x11:
        enc, body = "utf-16-be", raw[1:]
    elif first == 0x13:
        enc, body = "gb2312", raw[1:]
    elif first == 0x14:
        enc, body = "big5", raw[1:]
    elif first == 0x15:
        enc, body = "utf-8", raw[1:]
    else:
        body = raw[1:]
    try:
        text = body.decode(enc, "replace")
    except LookupError:
        text = body.decode("latin-1", "replace")
    return "".join(ch for ch in text if ch >= " " and not "\x80" <= ch <= "\x9f").strip()


# -- what it found -------------------------------------------------------------------


@dataclass(frozen=True)
class Indicator:
    key: str
    priority: int
    ref: str            # "1.4"
    name: str           # "Continuity_count_error"
    count: int          # since the start, or the last reset
    recent: int         # in the last RECENT seconds of the stream
    last: float | None  # wall clock of the last one


@dataclass(frozen=True)
class PidInfo:
    pid: int
    codec: str          # a key of CODEC_NAMES
    kind: str           # video, audio, subtitles, teletext, data, psi, null, unknown
    lang: str
    program: int | None
    bps: float          # over the window
    cc_errors: int
    scrambled: bool     # scrambled packets in the window
    packets: int


@dataclass(frozen=True)
class Program:
    number: int
    pmt_pid: int
    pcr_pid: int | None
    service: str
    provider: str
    ca: bool            # the PMT names a conditional-access system


@dataclass(frozen=True)
class Snapshot:
    state: str                      # waiting, running, not_ts, hls
    packets: int = 0
    seconds: float = 0.0            # of the stream analysed
    bps: float | None = None
    null_share: float | None = None
    tsid: int | None = None
    programs: tuple[Program, ...] = ()
    indicators: tuple[Indicator, ...] = ()
    pids: tuple[PidInfo, ...] = ()
    jitter: tuple[float | None, ...] = ()   # ms, one a second, oldest first
    jitter_now: float | None = None
    jitter_max: float | None = None
    pcr_max: float | None = None    # the longest PCR interval in the window, ms
    pcr_avg: float | None = None
    timed: bool = False             # arrival times known (the jitter)
    analysed: float = 1.0           # share of the time read (Runner)

    def service(self) -> str:
        for p in self.programs:
            if p.service:
                return p.service
        return ""

    def provider(self) -> str:
        for p in self.programs:
            if p.provider:
                return p.provider
        return ""


def verdict(snap: Snapshot | None) -> tuple[str, list[Indicator]]:
    """OK, WARN (a priority-2 indicator lately) or ERROR (priority 1), with
    the indicators behind it; WAITING before anything was read."""
    if snap is None or snap.state != "running" or not snap.packets:
        return WAITING, []
    p1 = [i for i in snap.indicators if i.priority == 1 and i.recent]
    if p1:
        return ERROR, p1
    p2 = [i for i in snap.indicators if i.priority == 2 and i.recent]
    if p2:
        return WARN, p2
    return OK, []


# -- the analysis ----------------------------------------------------------------------


class _Counter:
    __slots__ = ("count", "last", "secs")

    def __init__(self) -> None:
        self.count = 0
        self.last: float | None = None
        self.secs: deque[list[int]] = deque(maxlen=int(RECENT) + 2)

    def add(self, sec: int, wall: float) -> None:
        self.count += 1
        self.last = wall
        if self.secs and self.secs[-1][0] == sec:
            self.secs[-1][1] += 1
        else:
            self.secs.append([sec, 1])

    def recent(self, now_sec: int) -> int:
        return sum(n for s, n in self.secs if s > now_sec - RECENT)


class _Rep:
    """Something that must come at least every so often."""

    __slots__ = ("last", "flagged")

    def __init__(self, t: float | None = None) -> None:
        self.last = t
        self.flagged = False

    def seen(self, t: float, limit: float) -> bool:
        late = (self.last is not None and not self.flagged and t - self.last > limit)
        self.last = t
        self.flagged = False
        return late

    def overdue(self, t: float, limit: float) -> bool:
        if self.last is not None and not self.flagged and t - self.last > limit:
            self.flagged = True
            return True
        return False


class _Pid:
    __slots__ = ("pid", "n", "cc", "dup", "cc_err", "scr", "pcr", "pts",
                 "seen_n", "seen_t", "absent")

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.n = 0
        self.cc = -1
        self.dup = False
        self.cc_err = 0
        self.scr = 0
        self.pcr = -1
        self.pts = _Rep()
        self.seen_n = 0
        self.seen_t: float | None = None
        self.absent = False


@dataclass
class _Es:
    pid: int
    codec: str
    kind: str
    lang: str
    program: int


class Analyzer:
    """Feed it a transport stream's bytes as they come (``feed``), with when
    they arrived when that is known; ``snapshot`` says what it found."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._ind = {k: _Counter() for k, *_ in INDICATORS}
        self._pids: dict[int, _Pid] = {}
        self._buf = b""
        self._synced = False
        self._bad = 0
        self._ever_synced = False
        self._unsynced = 0
        self._hls = False
        self._damaged = 0
        # The tables.
        self._psi: set[int] = {0x0000, 0x0001, 0x0011}
        self._pes: set[int] = set()         # PIDs whose PTS is looked for
        self._asm: dict[int, bytearray] = {}
        self._cache: dict[tuple[int, int, int, int], bytes] = {}
        self._pat: dict[int, dict[int, int]] = {}     # section -> program -> PMT PID
        self._programs: dict[int, int] = {}            # program -> PMT PID
        self._pmt: dict[int, tuple[int | None, bool, list[_Es]]] = {}
        self._es: dict[int, _Es] = {}
        self._sdt: dict[int, tuple[str, str]] = {}
        self._tsid: int | None = None
        self._pat_rep = _Rep()
        self._pmt_rep: dict[int, _Rep] = {}
        self._cat = False
        # The stream's clock: its reference PCR (seconds since the start).
        self.t = 0.0
        self._ref = -1
        self._pcr_mode = False
        self._ref_wall = 0.0
        self._t0_wall: float | None = None
        self._next_sec = 1.0
        self._wall = 0.0
        # Arrival of the bytes being read (None: not known).
        self._arr_base: float | None = None
        self._spb = 0.0
        self._prev_arr: float | None = None
        self._jit: deque[tuple[float, float]] = deque(maxlen=4096)
        self._jit_sec: float | None = None
        self._jit_max: float | None = None
        self._jit_now: float | None = None
        self._jit_hist: deque[float | None] = deque(maxlen=HISTORY)
        self._timed = False
        self._iv_sec: float | None = None
        self._iv_hist: deque[tuple[float, float, int]] = deque(maxlen=WINDOW)
        self._iv_sum = 0.0
        self._iv_n = 0
        # Bitrate: (stream time, packets in all, {pid: packets}, {pid: scrambled}).
        self._packets = 0
        self._buckets: deque[tuple[float, int, dict[int, int], dict[int, int]]] = \
            deque(maxlen=WINDOW + 1)
        self._started = False

    # -- feeding -------------------------------------------------------------------

    def feed(self, data: bytes, arrival: float | None = None) -> None:
        """*data*: the next bytes of the stream. *arrival*: when they came
        (wall clock), None when they were read from what was kept."""
        if not data:
            return
        self._wall = self._clock()
        if self._t0_wall is None:
            self._t0_wall = self._wall
        if self._pcr_mode and self._wall - self._ref_wall > NO_PCR:
            # The reference PCR stopped: the clock on the wall carries on.
            self._pcr_mode = False
            self._t0_wall = self._wall - self.t
        prev, self._prev_arr = self._prev_arr, arrival
        b = self._buf + data if self._buf else bytes(data)
        if not self._ever_synced and not self._unsynced and b[:7] == b"#EXTM3U":
            self._hls = True
        if arrival is not None and prev is not None:
            spread = min(max(0.0, arrival - prev), SPREAD)
            self._spb = spread / len(data)
            # The bytes held over from the chunk before arrived before it.
            self._arr_base = arrival - spread - len(self._buf) * self._spb
            self._timed = True
        else:
            self._arr_base = None
        self._buf = b""
        if not self._pcr_mode:
            self.t = self._wall - self._t0_wall
        n = len(b)
        pos = 0
        while pos < n:
            if not self._synced:
                at = self._find_sync(b, pos)
                if at < 0:
                    keep = max(pos, n - SYNC_FIND * PACKET)
                    if not self._ever_synced:
                        self._unsynced += keep - pos
                    self._buf = b[keep:]
                    break
                if not self._ever_synced:
                    self._unsynced += at - pos
                self._synced = self._ever_synced = True
                self._bad = 0
                if not self._started:
                    self._started = True
                    self._pat_rep.last = self.t
                    self._buckets.append((self.t, 0, {}, {}))
                pos = at
            end = pos + (n - pos) // PACKET * PACKET
            if end == pos:
                self._buf = b[pos:]
                break
            pos = self._run(b, pos, end)
            if pos >= end:
                self._buf = b[end:]
                break
        if not self._pcr_mode and self.t >= self._next_sec:
            self._second()

    def _find_sync(self, b: bytes, pos: int) -> int:
        """Where five packets in a row begin, from *pos* on; -1: not in *b*."""
        last = len(b) - (SYNC_FIND - 1) * PACKET
        i = b.find(b"\x47", pos, max(pos, last))
        while 0 <= i < last:
            if all(b[i + k * PACKET] == SYNC for k in range(1, SYNC_FIND)):
                return i
            i = b.find(b"\x47", i + 1, last)
        return -1

    def gap(self) -> None:
        """What comes next does not follow on from what came before (a
        reconnect, a part skipped): the counters and tables carry on,
        nothing is counted across it."""
        self._buf = b""
        self._synced = False
        self._bad = 0
        self._prev_arr = None
        self._asm.clear()
        for st in self._pids.values():
            st.cc = -1
            st.dup = False
            st.pcr = -1
            st.pts.last = None
            st.seen_t = None
        self._jit.clear()
        self._pat_rep.last = self.t
        for rep in self._pmt_rep.values():
            rep.last = self.t

    def reset_counters(self) -> None:
        """Every count back to nothing; what the stream is stays known."""
        self._ind = {k: _Counter() for k, *_ in INDICATORS}
        for st in self._pids.values():
            st.cc_err = 0
        self._jit_hist.clear()
        self._jit_max = None
        self._iv_hist.clear()
        self._iv_sum, self._iv_n = 0.0, 0

    def _event(self, key: str) -> None:
        self._ind[key].add(int(self.t), self._wall)

    # -- the packets ----------------------------------------------------------------

    def _run(self, b: bytes, start: int, end: int) -> int:
        """The packets from *start* to *end*; where the sync was lost, else *end*."""
        get = self._pids.get
        psi = self._psi
        pes = self._pes
        bad = self._bad
        tei = 0
        packets = 0
        for i in range(start, end, PACKET):
            if b[i] != SYNC:
                bad += 1
                self._event("sync_byte")
                if bad >= SYNC_LOSE:
                    self._event("sync_loss")
                    self._synced = False
                    self._bad = 0
                    self._lost()
                    self._packets += packets
                    self._add_tei(tei)
                    return i
                continue
            bad = 0
            packets += 1
            h1 = b[i + 1]
            if h1 & 0x80:
                tei += 1
                continue
            pid = ((h1 & 0x1F) << 8) | b[i + 2]
            st = get(pid)
            if st is None:
                st = self._new_pid(pid)
                if st is None:
                    continue
            st.n += 1
            if pid == NULL_PID:
                continue
            h3 = b[i + 3]
            disc = 0
            if h3 & 0x20:
                alen = b[i + 4]
                if alen:
                    fl = b[i + 5]
                    if fl & 0x80:
                        disc = 1
                    if fl & 0x10 and alen >= 7:
                        self._pcr(st, b, i, disc)
            if h3 & 0x10:
                cc = h3 & 0x0F
                last = st.cc
                if cc != ((last + 1) & 0x0F):
                    if last >= 0 and not disc:
                        if cc == last and not st.dup:
                            st.dup = True
                        else:
                            st.cc_err += 1
                            self._event("cc")
                            if pid in psi:
                                self._asm.pop(pid, None)
                    else:
                        st.dup = False
                else:
                    st.dup = False
                st.cc = cc
                if h3 & 0xC0:
                    st.scr += 1
                    if pid in psi:
                        self._scrambled_table(pid)
                elif pid in psi:
                    self._table_packet(pid, b, i, h1 & 0x40, h3)
                elif h1 & 0x40 and pid in pes:
                    self._pes_start(st, b, i, h3)
            elif h3 & 0xC0:
                st.scr += 1
        self._bad = bad
        self._packets += packets
        self._add_tei(tei)
        return end

    def _add_tei(self, n: int) -> None:
        self._damaged += n
        for _ in range(n):
            self._event("transport")

    def _lost(self) -> None:
        for st in self._pids.values():
            st.cc = -1
            st.dup = False
        self._asm.clear()

    def _new_pid(self, pid: int) -> _Pid | None:
        if len(self._pids) >= MAX_PIDS and pid not in self._es and pid not in self._psi:
            return None
        st = self._pids[pid] = _Pid(pid)
        return st

    def _pcr(self, st: _Pid, b: bytes, i: int, disc: int) -> None:
        base = (b[i + 6] << 25) | (b[i + 7] << 17) | (b[i + 8] << 9) | (b[i + 9] << 1) | (b[i + 10] >> 7)
        pcr = base * 300 + (((b[i + 10] & 1) << 8) | b[i + 11])
        prev = st.pcr
        st.pcr = pcr
        jump = True
        d = 0
        if prev >= 0 and not disc:
            d = (pcr - prev) % PCR_WRAP
            if d > PCR_JUMP * PCR_HZ:
                self._event("pcr_disc")
            else:
                jump = False
                if d > PCR_LIMIT * PCR_HZ:
                    self._event("pcr_rep")
        if self._ref < 0:
            ref = self._pmt_pcr()
            self._ref = ref if ref is not None else st.pid
        if st.pid != self._ref:
            return
        self._pcr_mode = True
        self._ref_wall = self._wall
        if jump:
            self._jit.clear()
            return
        self.t += d / PCR_HZ
        iv = d / PCR_HZ
        self._iv_sum += iv
        self._iv_n += 1
        if self._iv_sec is None or iv > self._iv_sec:
            self._iv_sec = iv
        if self._arr_base is not None:
            self._jitter(self._arr_base + (i + PACKET) * self._spb)
        if self.t >= self._next_sec:
            self._second()

    def _jitter(self, arrival: float) -> None:
        off = arrival - self.t
        q = self._jit
        while q and q[-1][1] >= off:
            q.pop()
        q.append((arrival, off))
        while q[0][0] < arrival - JITTER_WINDOW:
            q.popleft()
        jit = off - q[0][1]
        self._jit_now = jit
        if self._jit_sec is None or jit > self._jit_sec:
            self._jit_sec = jit
        if self._jit_max is None or jit > self._jit_max:
            self._jit_max = jit
        if jit > JITTER_LIMIT:
            self._event("pcr_acc")

    def _pmt_pcr(self) -> int | None:
        for number in sorted(self._programs):
            got = self._pmt.get(number)
            if got is not None and got[0] is not None:
                return got[0]
        return None

    def _pes_start(self, st: _Pid, b: bytes, i: int, h3: int) -> None:
        p = i + 4
        if h3 & 0x20:
            p += 1 + b[i + 4]
        if p + 9 > i + PACKET:
            return
        if b[p] == 0 and b[p + 1] == 0 and b[p + 2] == 1 and b[p + 7] & 0x80:
            if st.pts.seen(self.t, PTS_LIMIT):
                self._event("pts")

    # -- the tables -----------------------------------------------------------------

    def _scrambled_table(self, pid: int) -> None:
        if pid == 0:
            self._event("pat")
        elif pid in self._pmt_rep:
            self._event("pmt")

    def _table_packet(self, pid: int, b: bytes, i: int, pusi: int, h3: int) -> None:
        p = i + 4
        end = i + PACKET
        if h3 & 0x20:
            p += 1 + b[i + 4]
        if p >= end:
            return
        asm = self._asm.get(pid)
        if pusi:
            ptr = b[p]
            p += 1
            if asm is not None and ptr:
                asm += b[p:min(end, p + ptr)]
                self._sections(pid, asm)
            p += ptr
            if p >= end:
                self._asm.pop(pid, None)
                return
            asm = bytearray(b[p:end])
            self._asm[pid] = asm
        elif asm is None:
            return
        else:
            asm += b[p:end]
        if not self._sections(pid, asm):
            self._asm.pop(pid, None)

    def _sections(self, pid: int, asm: bytearray) -> bool:
        """Every whole section at the head of *asm*, taken out of it; False
        when what is left is no section's start."""
        while len(asm) >= 3:
            if asm[0] == 0xFF:
                return False
            total = 3 + (((asm[1] & 0x0F) << 8) | asm[2])
            if total > MAX_SECTION:
                return False
            if len(asm) < total:
                return True
            sec = bytes(asm[:total])
            del asm[:total]
            self._section(pid, sec)
        return True

    def _section(self, pid: int, sec: bytes) -> None:
        tid = sec[0]
        if pid == 0 and tid != 0x00:
            self._event("pat")
            return
        if pid == 1 and tid != 0x01:
            self._event("cat")
            return
        long = bool(sec[1] & 0x80) and len(sec) >= 12
        if long:
            key = (pid, tid, (sec[3] << 8) | sec[4], sec[6])
            if self._cache.get(key) != sec:
                if crc32_mpeg(sec) != 0:
                    self._event("crc")
                    return
                if len(self._cache) >= MAX_CACHE:
                    self._cache.clear()
                self._cache[key] = sec
                self._parse(pid, tid, sec)
        if pid == 0:
            if self._pat_rep.seen(self.t, PAT_LIMIT):
                self._event("pat")
        elif pid == 1:
            self._cat = True
        elif tid == 0x02 and pid in self._pmt_rep:
            if self._pmt_rep[pid].seen(self.t, PMT_LIMIT):
                self._event("pmt")

    def _parse(self, pid: int, tid: int, sec: bytes) -> None:
        try:
            if pid == 0:
                self._parse_pat(sec)
            elif tid == 0x02 and pid in self._pmt_rep:
                self._parse_pmt(sec)
            elif pid == 0x11 and tid == 0x42:
                self._parse_sdt(sec)
        except IndexError:
            log.debug("TS analysis: a short table on PID %d", pid)

    def _parse_pat(self, sec: bytes) -> None:
        self._tsid = (sec[3] << 8) | sec[4]
        progs: dict[int, int] = {}
        for j in range(8, len(sec) - 4 - 3, 4):
            number = (sec[j] << 8) | sec[j + 1]
            pmt = ((sec[j + 2] & 0x1F) << 8) | sec[j + 3]
            if number and len(progs) < MAX_PROGRAMS:
                progs[number] = pmt
        self._pat[sec[6]] = progs
        if sec[6] > sec[7]:
            return
        for s in [s for s in self._pat if s > sec[7]]:
            del self._pat[s]
        programs: dict[int, int] = {}
        for part in self._pat.values():
            programs.update(part)
        if programs == self._programs:
            return
        self._programs = programs
        pmts = set(programs.values())
        for gone in [p for p in self._pmt_rep if p not in pmts]:
            del self._pmt_rep[gone]
        for p in pmts:
            self._pmt_rep.setdefault(p, _Rep(self.t))
        for number in [n for n in self._pmt if n not in programs]:
            del self._pmt[number]
        self._psi.clear()
        self._psi.update({0x0000, 0x0001, 0x0011}, pmts)
        self._index_es()

    def _parse_pmt(self, sec: bytes) -> None:
        number = (sec[3] << 8) | sec[4]
        if number not in self._programs:
            return
        pcr = ((sec[8] & 0x1F) << 8) | sec[9]
        info = ((sec[10] & 0x0F) << 8) | sec[11]
        ca = any(tag == 0x09 for tag, _ in _descriptors(sec[12:12 + info]))
        es: list[_Es] = []
        j = 12 + info
        stop = len(sec) - 4
        while j + 5 <= stop:
            stype = sec[j]
            epid = ((sec[j + 1] & 0x1F) << 8) | sec[j + 2]
            n = ((sec[j + 3] & 0x0F) << 8) | sec[j + 4]
            descs = list(_descriptors(sec[j + 5:j + 5 + n]))
            codec, kind, lang = classify(stype, descs)
            ca = ca or any(tag == 0x09 for tag, _ in descs)
            es.append(_Es(epid, codec, kind, lang, number))
            j += 5 + n
        self._pmt[number] = (pcr if pcr != NULL_PID else None, ca, es)
        if self._ref < 0 or self._ref not in (p for p, _c, _e in self._pmt.values()):
            ref = self._pmt_pcr()
            if ref is not None:
                self._ref = ref
        self._index_es()

    def _index_es(self) -> None:
        self._es = {e.pid: e for _p, _c, es in self._pmt.values() for e in es}
        self._pes.clear()
        self._pes.update(e.pid for e in self._es.values() if e.kind in _STEADY)

    def _parse_sdt(self, sec: bytes) -> None:
        j = 11
        stop = len(sec) - 4
        while j + 5 <= stop:
            sid = (sec[j] << 8) | sec[j + 1]
            n = ((sec[j + 3] & 0x0F) << 8) | sec[j + 4]
            for tag, d in _descriptors(sec[j + 5:j + 5 + n]):
                if tag == 0x48 and len(d) >= 3:
                    pl = d[1]
                    provider = dvb_text(d[2:2 + pl])
                    nl = d[2 + pl] if len(d) > 2 + pl else 0
                    name = dvb_text(d[3 + pl:3 + pl + nl])
                    if len(self._sdt) < MAX_PROGRAMS or sid in self._sdt:
                        self._sdt[sid] = (name[:80], provider[:80])
            j += 5 + n

    # -- once a second of the stream ------------------------------------------------

    def _second(self) -> None:
        t = self.t
        self._next_sec = float(int(t) + 1)
        sec = int(t)
        pids = self._pids
        # In all: every PID's packets and the damaged ones (counted as they
        # come, not at the end of the chunk being read).
        by_pid = {p: s.n for p, s in pids.items()}
        self._buckets.append((t, sum(by_pid.values()) + self._damaged, by_pid,
                              {p: s.scr for p, s in pids.items()}))
        if self._pat_rep.overdue(t, PAT_LIMIT):
            self._ind["pat"].add(sec, self._wall)
        for rep in self._pmt_rep.values():
            if rep.overdue(t, PMT_LIMIT):
                self._ind["pmt"].add(sec, self._wall)
        for e in self._es.values():
            st = pids.get(e.pid)
            if st is None:
                st = self._new_pid(e.pid)
                if st is None:
                    continue
            if st.n != st.seen_n or st.seen_t is None:
                st.seen_n, st.seen_t, st.absent = st.n, t, False
            elif e.kind in _STEADY and not st.absent and t - st.seen_t > PID_LIMIT:
                st.absent = True
                self._ind["pid"].add(sec, self._wall)
            if e.kind in _STEADY and not st.scr and st.pts.overdue(t, PTS_LIMIT):
                self._ind["pts"].add(sec, self._wall)
        if not self._cat and len(self._buckets) >= 2:
            scr = sum(self._buckets[-1][3].values()) - sum(self._buckets[-2][3].values())
            if scr > 0:
                self._ind["cat"].add(sec, self._wall)
        self._jit_hist.append(None if self._jit_sec is None else self._jit_sec * 1000.0)
        self._jit_sec = None
        if self._iv_sec is not None:
            self._iv_hist.append((self._iv_sec, self._iv_sum, self._iv_n))
        self._iv_sec, self._iv_sum, self._iv_n = None, 0.0, 0

    # -- what it found -------------------------------------------------------------

    def state(self) -> str:
        if self._ever_synced:
            return "running"
        if self._hls:
            return "hls"
        if self._unsynced >= NOT_TS_BYTES:
            return "not_ts"
        return "waiting"

    def snapshot(self) -> Snapshot:
        state = self.state()
        sec = int(self.t)
        inds = tuple(Indicator(k, prio, ref, name, self._ind[k].count,
                               self._ind[k].recent(sec), self._ind[k].last)
                     for k, prio, ref, name in INDICATORS)
        if state != "running":
            return Snapshot(state, indicators=inds)
        bps: float | None = None
        rates: dict[int, float] = {}
        scrambled: set[int] = set()
        null_share: float | None = None
        if len(self._buckets) >= 2:
            t0, n0, by0, scr0 = self._buckets[0]
            t1, n1, by1, scr1 = self._buckets[-1]
            span = t1 - t0
            if span > 0:
                bps = (n1 - n0) * PACKET * 8 / span
                for pid, n in by1.items():
                    rates[pid] = (n - by0.get(pid, 0)) * PACKET * 8 / span
                if n1 > n0:
                    null_share = (by1.get(NULL_PID, 0) - by0.get(NULL_PID, 0)) / (n1 - n0)
            scrambled = {p for p, n in scr1.items() if n > scr0.get(p, 0)}
        pmt_pids = {pmt: number for number, pmt in self._programs.items()}
        pids: list[PidInfo] = []
        for pid in sorted(self._pids):
            st = self._pids[pid]
            e = self._es.get(pid)
            if e is not None:
                codec, kind, lang, prog = e.codec, e.kind, e.lang, e.program
            elif pid in pmt_pids:
                codec, kind, lang, prog = "pmt", "psi", "", pmt_pids[pid]
            elif pid in _RESERVED:
                codec = _RESERVED[pid]
                kind, lang, prog = ("null" if pid == NULL_PID else "psi"), "", None
            else:
                codec, kind, lang, prog = "unknown", "unknown", "", None
            pids.append(PidInfo(pid, codec, kind, lang, prog, rates.get(pid, 0.0), st.cc_err,
                                pid in scrambled, st.n))
        programs = []
        for number in sorted(self._programs):
            pcr, ca, _es = self._pmt.get(number, (None, False, []))
            name, provider = self._sdt.get(number, ("", ""))
            programs.append(Program(number, self._programs[number], pcr, name, provider, ca))
        ivs = list(self._iv_hist)
        n_iv = sum(n for _m, _s, n in ivs)
        return Snapshot(
            state, packets=self._packets, seconds=self.t, bps=bps, null_share=null_share,
            tsid=self._tsid, programs=tuple(programs), indicators=inds, pids=tuple(pids),
            jitter=tuple(self._jit_hist),
            jitter_now=None if self._jit_now is None else self._jit_now * 1000.0,
            jitter_max=None if self._jit_max is None else self._jit_max * 1000.0,
            pcr_max=max(m for m, _s, _n in ivs) * 1000.0 if ivs else None,
            pcr_avg=sum(s for _m, s, _n in ivs) / n_iv * 1000.0 if n_iv else None,
            timed=self._timed)


def _descriptors(raw: bytes) -> Iterator[tuple[int, bytes]]:
    j = 0
    while j + 2 <= len(raw):
        tag, n = raw[j], raw[j + 1]
        yield tag, raw[j + 2:j + 2 + n]
        j += 2 + n


def classify(stype: int, descs: Iterable[tuple[int, bytes]]) -> tuple[str, str, str]:
    """(codec, kind, language) of an elementary stream from its PMT entry."""
    codec, kind = STREAM_TYPES.get(stype, ("unknown", "data"))
    lang = ""
    dvb: tuple[str, str] | None = None
    registered: tuple[str, str] | None = None
    for tag, d in descs:
        if tag in (0x0A, 0x59, 0x56) and len(d) >= 3 and not lang:
            lang = d[:3].decode("latin-1", "replace").strip()
        if tag == 0x05 and len(d) >= 4:
            registered = registered or _REGISTRATION.get(bytes(d[:4]))
        elif tag in _DESCRIPTOR:
            dvb = dvb or _DESCRIPTOR[tag]
        elif tag == 0x7F and d[:1] == b"\x15":
            dvb = dvb or ("ac4", "audio")
    found = dvb or registered
    if found is not None and codec in ("private", "sections", "unknown"):
        codec, kind = found
    return codec, kind, "".join(ch for ch in lang if ch.isalnum())


# -- reading the rewind buffer ----------------------------------------------------------


class _Seg(Protocol):
    seq: int


class Store(Protocol):
    """What the runner reads (core/dvr_store.SegmentStore)."""

    @property
    def closed(self) -> bool: ...

    def newest(self) -> Any: ...

    def first_from(self, seq: int) -> Any: ...

    def read_from(self, seq: int, chunk: int = ..., wait: float = ...,
                  stop: Callable[[], bool] | None = ...,
                  on_segment: Callable[[Any], None] | None = ...) -> Iterator[bytes]: ...


class Runner:
    """Reads the newest bytes of a live channel's rewind buffer into an
    Analyzer, on a thread of its own, and publishes a snapshot once a
    second (``latest``). Its work is capped: past BUDGET seconds of the
    processor in a second, the rest of that segment is let go and the
    analysis goes on from the next (said in the snapshot's ``analysed``)."""

    CHUNK = 256 * 1024
    WAIT = 0.25
    BUDGET = 0.25

    def __init__(self, store: Store, analyzer: Analyzer | None = None,
                 clock: Callable[[], float] = time.time,
                 cpu: Callable[[], float] = time.thread_time) -> None:
        self.store = store
        self.analyzer = analyzer or Analyzer(clock)
        self._clock = clock
        self._cpu = cpu
        self._stop = threading.Event()
        self._reset = False
        self.latest: Snapshot | None = None
        self.ended = False
        self.last_data: float | None = None
        self.started = clock()
        self._skipped = 0.0
        self._seq = -1
        self._thread = threading.Thread(target=self._run, name="ts-analysis", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def reset(self) -> None:
        """Counters back to nothing, on the runner's own thread."""
        self._reset = True

    def idle(self) -> float:
        """Seconds since bytes last came (since the start, before any did)."""
        return self._clock() - (self.last_data if self.last_data is not None else self.started)

    def _stopping(self) -> bool:
        return self._stop.is_set()

    def _on_segment(self, seg: _Seg) -> None:
        self._seq = seg.seq

    def _apply_reset(self) -> None:
        if self._reset:
            self._reset = False
            self.analyzer.reset_counters()

    def _publish(self) -> None:
        self._apply_reset()
        elapsed = max(1e-6, self._clock() - self.started)
        share = max(0.0, min(1.0, 1.0 - self._skipped / elapsed))
        self.latest = replace(self.analyzer.snapshot(), analysed=share)

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            log.debug("TS analysis: the reader ended", exc_info=True)
        finally:
            self.ended = True
            self._publish()

    def _loop(self) -> None:
        published = 0.0
        skipping: float | None = None       # since when a part is let go
        while not self._stop.is_set():
            if self.store.closed:
                return
            seg = self.store.newest() if self._seq < 0 else self.store.first_from(self._seq + 1)
            if seg is None:
                self._stop.wait(self.WAIT)
                if self._clock() - published >= 1.0:
                    published = self._clock()
                    self._publish()
                continue
            gen = self.store.read_from(seg.seq, chunk=self.CHUNK, wait=self.WAIT,
                                       stop=self._stopping, on_segment=self._on_segment)
            caught_up = False
            second = self._clock()
            cpu0 = self._cpu()
            over = False
            try:
                for data in gen:
                    if self._stop.is_set():
                        break
                    now = self._clock()
                    if skipping is not None:
                        self._skipped += max(0.0, now - skipping)
                        skipping = None
                    self.last_data = now
                    self._apply_reset()
                    self.analyzer.feed(data, now if caught_up else None)
                    caught_up = len(data) < self.CHUNK
                    if now - published >= 1.0:
                        published = now
                        self._publish()
                    if now - second >= 1.0:
                        second, cpu0 = now, self._cpu()
                    elif self._cpu() - cpu0 > self.BUDGET:
                        over = True
                        break
            finally:
                close = getattr(gen, "close", None)
                if close is not None:
                    close()
            self.analyzer.gap()
            if over:
                # Past its share of the processor: the rest of this second
                # and of the segment let go, on from the next one.
                skipping = self._clock()
                self._stop.wait(max(0.0, 1.0 - (skipping - second)))
            elif not self._stop.is_set():
                self._stop.wait(self.WAIT / 5)
