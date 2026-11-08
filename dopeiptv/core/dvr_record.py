"""A recording made from an application-kept rewind buffer.

It copies the store's bytes into a file from a segment on and follows the
channel live: a pause, a rewind or another channel on screen changes
nothing for it, and it needs no connection of its own - the buffer's one
provider connection feeds both. It can begin in the past, as far back as
the buffer holds. Across a reconnect gap it goes on with the next run.
Stopped, it writes what the buffer holds up to then and ends.

A file begins on a key frame (KeyframeStart): one that begins between
two shows nothing - black or grey in many players - until the next.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .dvr_store import Segment, SegmentStore
from .log import log


TS = 188


def _pid(pkt: bytes) -> int:
    return ((pkt[1] & 0x1F) << 8) | pkt[2]


def _random_access(pkt: bytes) -> bool:
    """The packet starts a payload unit and its adaptation field says a
    decoder can start here: on a video PID, a key frame."""
    if not pkt[1] & 0x40 or not pkt[3] & 0x20:
        return False
    return pkt[4] > 0 and bool(pkt[5] & 0x40)


def _section(pkt: bytes, table_id: int) -> bytes | None:
    """The PSI section a single packet starts (PAT or PMT), up to its CRC."""
    if not pkt[1] & 0x40:
        return None
    i = 4
    if pkt[3] & 0x20:                       # an adaptation field first
        i += 1 + pkt[4]
    if i >= TS:
        return None
    i += 1 + pkt[i]                         # the pointer field
    if i + 3 > TS or pkt[i] != table_id:
        return None
    length = ((pkt[i + 1] & 0x0F) << 8) | pkt[i + 2]
    end = i + 3 + length - 4                # without the CRC
    return pkt[i:end] if end <= TS else None


def _pmt_pids(pat: bytes) -> set[int]:
    """The PMT PIDs a single-packet PAT names."""
    sec = _section(pat, 0x00)
    out: set[int] = set()
    if sec is None:
        return out
    for j in range(8, len(sec) - 3, 4):
        if (sec[j] << 8) | sec[j + 1]:      # program 0 is the network PID
            out.add(((sec[j + 2] & 0x1F) << 8) | sec[j + 3])
    return out


# MPEG-1/2, MPEG-4 part 2, H.264, its SVC/MVC, HEVC, VVC, AVS, VC-1.
VIDEO_TYPES = {0x01, 0x02, 0x10, 0x1B, 0x1F, 0x20, 0x24, 0x33, 0x42, 0xEA}


def _video_pids(pmt: bytes) -> set[int] | None:
    """The video PIDs a single-packet PMT lists; None when it is no PMT."""
    sec = _section(pmt, 0x02)
    if sec is None or len(sec) < 12:
        return None
    out: set[int] = set()
    j = 12 + (((sec[10] & 0x0F) << 8) | sec[11])   # past the program info
    while j + 5 <= len(sec):
        stype = sec[j]
        pid = ((sec[j + 1] & 0x1F) << 8) | sec[j + 2]
        if stype in VIDEO_TYPES:
            out.add(pid)
        j += 5 + (((sec[j + 3] & 0x0F) << 8) | sec[j + 4])
    return out


class KeyframeStart:
    """What a recording writes first: from the first key frame of the video
    on, with the stream's newest PAT and PMT before it, so the file plays
    from its first frame. Without video (radio) it starts where a decoder
    may. A stream that marks no start within LIMIT bytes - or that is not
    on packet boundaries - is written whole, as before."""

    LIMIT = 16 * 1024 * 1024

    def __init__(self) -> None:
        self._buf = bytearray()
        self._done = False
        self._pat: bytes | None = None
        self._pmt_pids: set[int] = set()
        self._pmts: dict[int, bytes] = {}
        self._video: set[int] = set()
        self._scanned = 0
        self.skipped = 0

    def feed(self, data: bytes) -> bytes:
        if self._done:
            return data
        self._buf += data
        n = len(self._buf) // TS * TS
        start, self._scanned = self._scanned, n
        for off in range(start, n, TS):
            pkt = bytes(self._buf[off:off + TS])
            if pkt[0] != 0x47:              # not on packets: leave it be
                return self._give_up()
            pid = _pid(pkt)
            if pid == 0:
                self._pat = pkt
                self._pmt_pids = _pmt_pids(pkt) or self._pmt_pids
                continue
            if pid in self._pmt_pids:
                video = _video_pids(pkt)
                if video is not None:
                    self._pmts[pid] = pkt
                    self._video = video
                continue
            if not self._pmts or not _random_access(pkt):
                continue
            if self._video and pid not in self._video:
                continue
            head = (self._pat or b"") + b"".join(self._pmts.values())
            rest = bytes(self._buf[off:])
            self.skipped = off
            self._buf = bytearray()
            self._done = True
            return head + rest
        if len(self._buf) > self.LIMIT:
            return self._give_up()
        return b""

    def _give_up(self) -> bytes:
        out = bytes(self._buf)
        self._buf = bytearray()
        self._done = True
        return out

    def flush(self) -> bytes:
        """What is held back when the recording ends before a key frame."""
        return self._give_up() if not self._done else b""


class BufferRecorder:
    POLL = 0.5          # waiting for the next run after a reconnect gap

    def __init__(self, store: SegmentStore, path: str | Path, first: Segment,
                 on_done: Callable[["BufferRecorder"], None] | None = None
                 ) -> None:
        self.store = store
        self.path = Path(path)
        self.first = first
        self.on_done = on_done
        self.bytes = 0
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dvr-record",
                                        daemon=True)

    def start(self) -> "BufferRecorder":
        self._thread.start()
        return self

    def stop(self, wait: float = 0.0) -> None:
        """Write what the buffer holds now, then end."""
        self._stop.set()
        if wait and self._thread.is_alive():
            self._thread.join(wait)

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        last = [self.first]

        def moved(seg: Segment) -> None:
            last[0] = seg

        start = KeyframeStart()
        try:
            with open(self.path, "ab") as fh:
                seq: int | None = self.first.seq
                while seq is not None:
                    for chunk in self.store.read_from(
                            seq, stop=self._stop.is_set, on_segment=moved):
                        out = start.feed(chunk)
                        if out:
                            fh.write(out)
                            self.bytes += len(out)
                    seq = None
                    # The run ended (a reconnect), the reader fell behind
                    # retention, the store closed - or it was stopped.
                    while not self.store.closed:
                        nxt = self.store.first_from(last[0].seq + 1)
                        if nxt is not None:
                            seq = nxt.seq
                            break
                        if self._stop.is_set():
                            break
                        self._stop.wait(self.POLL)
                tail = start.flush()
                if tail:
                    fh.write(tail)
                    self.bytes += len(tail)
                if start.skipped:
                    log.info("DVR recording: began on a key frame, %d bytes "
                             "in", start.skipped)
        except OSError as e:
            self.error = str(e)
            log.warning("DVR recording: %s", e)
        finally:
            log.info("DVR recording ended: %.1f MB", self.bytes / 1e6)
            if self.on_done is not None:
                try:
                    self.on_done(self)
                except Exception as e:
                    log.debug("DVR recording done callback: %s", e)
