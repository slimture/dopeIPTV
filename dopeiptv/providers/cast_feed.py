"""One stream for a cast out of many pieces.

The provider's archive is asked for stretch by stretch - each stretch ends
where the panel had written up to - and the app's own rewind buffer
(core/dvr_*) is a run of segments until a reconnect starts the next. Handed
to a TV piece by piece, each end was the end of the title: the TV stopped,
the app asked for the next stretch and the converter started again, in the
middle of a GOP every time ("non-existing PPS 0 referenced").

A Feed reads the pieces here, one after the other, and writes them into the
input of ONE converter run (providers/cast_bridge.py for a Chromecast,
providers/airplay_bridge.py for an Apple TV), so the TV is handed one
growing playlist and plays on - and can go back within what is written:

    piece 1 --reader ffmpeg--.
    piece 2 --reader ffmpeg--+--> stdin of the converter --> HLS --> TV
    ...

Each reader copies its piece as MPEG-TS: from its first keyframe (a stream
copy drops what comes before one), with SPS/PPS in front of every keyframe
(dump_extra), and its clock carried on from where the last piece ended
(-output_ts_offset), so the converter sees one stream. The first piece's
sound from before that keyframe is left out here (LeadTrim): a stream
whose sound starts up to a GOP before its picture is what an HLS player
plays out of sync. Whatever the piece's
address is - the provider's, or the rewind buffer's loopback server - only
ffmpeg here reads it; the TV only ever sees the converter's playlist on the
LAN.
"""
from __future__ import annotations

import subprocess
import threading
import time
from typing import IO, Any, Callable
from urllib.parse import urlsplit

from ..core.log import log, redact_url
from ..core.player_exec import NO_WINDOW

#: What the converter reads instead of an address.
INPUT = "pipe:0"
#: Its input options: a transport stream on stdin, probed as an archive
#: window is (a piece can open far from a keyframe's SPS).
INPUT_OPTIONS = ["-analyzeduration", "8000000", "-probesize", "8000000",
                 "-f", "mpegts"]
#: The readers' input: timestamps made where a panel left none, and what
#: the network broke dropped before it reaches the converter.
KEYFRAME_INPUT = ["-fflags", "+genpts+discardcorrupt"]
#: Every keyframe carries its SPS/PPS, so the converter - and the TV - can
#: start decoding at the first one of every piece.
KEYFRAME_OUTPUT = ["-bsf:v", "dump_extra=freq=keyframe"]
#: One sound frame per PES packet, so the sound before the first picture
#: can be left out to the frame (LeadTrim) - the default packs a fifth of
#: a second into one.
FRAME_PES = ["-pes_payload_size", "0"]
#: A little room between one piece's clock and the next, so their frames
#: never overlap (which the converter would have to squeeze).
JOIN_GAP_S = 0.25
RETRIES = 3             # a piece that gives nothing, far from live
NEAR_LIVE_WAIT_S = 15.0
NEXT_WAIT_S = 5.0       # the buffer's next run not begun yet

#: (address, seconds into it to start, the wall-clock moment it starts at)
Piece = tuple[str, float, float]
UrlAt = Callable[[float], "Piece | None"]
NetFor = Callable[[str], list[str]]


def is_loopback(url: str) -> bool:
    """An address on this computer: the rewind buffer's own server, a
    local proxy. No TV can ever reach one."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.")


def reader_args(exe: str, url: str, start: float = 0.0, ts_offset: float = 0.0,
                net: list[str] | None = None, audio: int = 0) -> list[str]:
    """The command that copies one piece to stdout as MPEG-TS, reporting
    how far it got on stderr (-progress: out_time_us=...).

    Only the picture and the one sound track the cast plays (*audio*, what
    -map 0:a:N counts): a second track with no packets in the probe (an
    audio description that is silent for now) has no sample rate, and the
    TS muxer then refuses to write anything at all."""
    return [
        exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-nostats",
        "-progress", "pipe:2",
        "-analyzeduration", "8000000", "-probesize", "8000000",
        *(net or []),
        *(["-ss", f"{start:.3f}"] if start > 0 else []),
        *KEYFRAME_INPUT, "-i", url,
        "-map", "0:v:0?", "-map", f"0:a:{max(0, int(audio))}?", "-sn", "-dn",
        "-c", "copy", *KEYFRAME_OUTPUT, *FRAME_PES,
        *(["-output_ts_offset", f"{ts_offset:.3f}"] if ts_offset > 0 else []),
        "-f", "mpegts", "pipe:1",
    ]


def progress_seconds(line: str) -> float | None:
    """The output time in an ffmpeg -progress line, in seconds, or None."""
    key, _, value = line.strip().partition("=")
    try:
        if key in ("out_time_us", "out_time_ms"):    # both microseconds
            return max(0.0, int(value) / 1e6)
        if key == "out_time":
            h, m, s = value.split(":")
            return max(0.0, int(h) * 3600 + int(m) * 60 + float(s))
    except ValueError:
        return None
    return None


class Feed:
    """The pieces of one broadcast from *start* (wall-clock seconds) on,
    as one transport stream for one converter run at a time.

    *url_at(moment)* gives the piece that holds *moment*: its address,
    where in it to start, and the moment that start is. *lag*: how far
    behind now a piece may be asked from (the archive cannot serve the
    minute it is still writing; the buffer follows live, 0). *on_close*:
    called once by close() - the rewind buffer's hold let go.

    A bridge attaches the feed to its converter's stdin and detaches it
    when it stops; attached again (a second try), it starts over from
    *start*. Only the window closes it, when the cast is done with it.
    """

    def __init__(self, url_at: UrlAt, start: float, lag: float = 0.0,
                 kind: str = "archive",
                 on_close: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.url_at, self.start, self.lag, self.kind = url_at, start, lag, kind
        self._on_close = on_close
        self._clock = clock
        self.first: Piece | None = url_at(start)
        #: The moment the playlist's zero is: where the first piece starts.
        self.first_wall = self.first[2] if self.first else float(start)
        #: How far the pieces read so far reach, wall clock.
        self.reached = self.first_wall
        self.pieces = 0
        self.audio = 0
        self.closed = False
        #: What the pieces come from, for whoever asks again from another
        #: moment (the rewind buffer's session).
        self.source: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    @property
    def first_url(self) -> str:
        return self.first[0] if self.first else ""

    def attach(self, sink: IO[bytes], exe: str,
               net_for: NetFor | None = None, audio: int = 0) -> None:
        """Begin writing the pieces into *sink* (a converter's stdin), on a
        thread of its own; *sink* is closed when they run out. *audio*: the
        one sound track the pieces carry (the converter reads it as 0:a:0)."""
        self.detach()
        self.audio = audio
        if self.closed:
            raise RuntimeError("the feed was closed")
        stop = threading.Event()
        self._stop = stop
        self.reached, self.pieces = self.first_wall, 0
        t = threading.Thread(target=self._run,
                             args=(sink, exe, net_for or (lambda _u: []), stop),
                             name="cast-feed", daemon=True)
        self._thread = t
        t.start()

    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def detach(self) -> None:
        """Stop writing: the reader ends, the converter is the bridge's."""
        self._stop.set()
        with self._lock:
            reader = self._reader
        if reader is not None:
            _end(reader)
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(3.0)
        self._thread = None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.detach()
        cb, self._on_close = self._on_close, None
        if cb is not None:
            try:
                cb()
            except Exception as e:
                log.debug("cast feed: letting go: %s", e)

    # -- the pump ----------------------------------------------------------------

    def _run(self, sink: IO[bytes], exe: str, net_for: NetFor,
             stop: threading.Event) -> None:
        piece, media, fails = self.first, 0.0, 0
        try:
            while not stop.is_set():
                if piece is None:
                    piece = self._next(stop)
                    if piece is None:
                        break
                url, at, wall = piece
                # Only where the stream begins: at a join the sound plays
                # on from the piece before.
                trim = None if self.pieces else LeadTrim()
                got = self._read(sink, exe, url, at,
                                 media + (JOIN_GAP_S if self.pieces else 0.0),
                                 net_for(url), stop, trim)
                if got is None or stop.is_set():
                    break                       # the converter went away
                if got < 0.5:
                    # Asked again from the same moment: near live the panel
                    # has simply not written it yet; further back, a few
                    # tries and the stream ends (the TV plays what it has).
                    piece = None
                    if wall >= self._clock() - 2 * self.lag - 60:
                        stop.wait(NEAR_LIVE_WAIT_S)
                        continue
                    fails += 1
                    if fails > RETRIES:
                        log.info("cast feed: the %s gave nothing %d times - "
                                 "the stream ends here", self.kind, fails)
                        break
                    stop.wait(2.0 * fails)
                    continue
                fails = 0
                media += got + (JOIN_GAP_S if self.pieces else 0.0)
                self.pieces += 1
                self.reached = wall + got
                log.info("cast feed: %s piece %d ended %.0f s in (%.0f s "
                         "behind live) - the next goes on the same playlist",
                         self.kind, self.pieces, got,
                         max(0.0, self._clock() - self.reached))
                piece = None
        except Exception as e:
            log.info("cast feed: stopped (%s: %s)", type(e).__name__, e)
        finally:
            try:
                sink.close()
            except Exception:
                pass

    def _next(self, stop: threading.Event) -> Piece | None:
        """The piece after what has been read, once there is one."""
        while not stop.is_set():
            wait = self.reached + self.lag - self._clock()
            if wait > 0:
                stop.wait(min(wait, NEXT_WAIT_S))
                continue
            try:
                piece = self.url_at(self.reached)
            except Exception as e:
                log.info("cast feed: no next piece (%s)", e)
                return None
            if piece is not None:
                return piece
            stop.wait(NEXT_WAIT_S)
        return None

    def _read(self, sink: IO[bytes], exe: str, url: str, at: float,
              ts_offset: float, net: list[str],
              stop: threading.Event,
              trim: LeadTrim | None = None) -> float | None:
        """Copy one piece into *sink*: how many seconds it gave, or None
        when *sink* is gone. *trim*: its sound from before its first
        picture left out."""
        args = reader_args(exe, url, at, ts_offset, net, self.audio)
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    creationflags=NO_WINDOW)
        except OSError as e:
            log.info("cast feed: the reader did not start (%s)", e)
            return 0.0
        with self._lock:
            self._reader = proc
        if stop.is_set():
            _end(proc)
        log.info("cast feed: reading the %s from %s", self.kind,
                 redact_url(url))
        began = time.monotonic()
        out: list[float] = [0.0]
        errs = threading.Thread(target=_progress, args=(proc, out), daemon=True)
        errs.start()
        gone = False
        stdout = proc.stdout
        try:
            while stdout is not None:
                raw = stdout.read(65536)
                if began and raw:
                    # What the wait before a seek's stream is made of: the
                    # provider answering, and ffmpeg's look into the piece.
                    log.info("cast feed: the %s gave its first picture and "
                             "sound after %.1f s", self.kind,
                             time.monotonic() - began)
                    began = 0.0
                chunk = raw
                if trim is not None:
                    chunk = trim.feed(raw) if raw else trim.flush()
                    if trim.done:
                        if trim.lead > 0:
                            log.info("cast feed: %.2f s of sound before the "
                                     "first picture left out", trim.lead)
                        trim = None
                if chunk:
                    try:
                        sink.write(chunk)
                    except (OSError, ValueError):
                        gone = True
                        break
                if not raw:
                    break
        except Exception:
            pass
        finally:
            if gone or stop.is_set():
                _end(proc)
            try:
                proc.wait(timeout=5)
            except Exception:
                _end(proc)
            errs.join(2.0)
            with self._lock:
                if self._reader is proc:
                    self._reader = None
        return None if gone else out[0]


def _progress(proc: subprocess.Popen[bytes], out: list[float]) -> None:
    said = 0
    try:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            secs = progress_seconds(line)
            if secs is not None:
                out[0] = max(out[0], secs)
            elif line and "=" not in line and said < 10:
                said += 1
                log.info("cast feed: ffmpeg: %s", line)
    except Exception:
        pass


def _end(proc: subprocess.Popen[Any]) -> None:
    for step, grace in ((proc.terminate, 1.0), (proc.kill, 0.5)):
        try:
            step()
            proc.wait(timeout=grace)
            return
        except Exception:
            continue


# -- the sound before the first picture -----------------------------------------

_TS = 188
_PTS_WRAP = 1 << 33
#: PMT stream types that are a picture (MPEG-1/2, MPEG-4, H.264, HEVC, VVC).
_PICTURE_TYPES = frozenset({0x01, 0x02, 0x10, 0x1B, 0x24, 0x33})
#: Sound held back at most this long waiting for the first picture: a
#: stream whose picture never comes (radio with a dead picture PID) plays.
LEAD_MAX_S = 20.0
LEAD_MAX_BYTES = 4 << 20
_AFTER_MAX = 20000          # packets looked at once the picture has begun


def _payload(pkt: bytes) -> bytes:
    afc = (pkt[3] >> 4) & 3
    if not afc & 1:
        return b""
    return pkt[5 + pkt[4]:] if afc & 2 else pkt[4:]


def _pes_pts(pkt: bytes) -> int | None:
    """The PTS (90 kHz) of the PES packet that starts in *pkt*, if any."""
    if not pkt[1] & 0x40:
        return None
    p = _payload(pkt)
    if len(p) < 14 or p[:3] != b"\x00\x00\x01" or not p[7] & 0x80:
        return None
    return (((p[9] >> 1) & 7) << 30 | p[10] << 22 | (p[11] >> 1) << 15
            | p[12] << 7 | p[13] >> 1)


def _before(a: int, b: int) -> bool:
    """PTS *a* is earlier than *b*, across the 33-bit wrap."""
    return 0 < (b - a) % _PTS_WRAP < _PTS_WRAP // 2


class LeadTrim:
    """A transport stream without the sound that precedes its first picture.

    A piece is joined in the middle of a GOP: the copy drops the picture up
    to its first keyframe, the sound starts at once - up to a GOP earlier.
    Written as fragmented MP4, that start is one track's offset against the
    other, which an Apple TV plays as sound out of step with the picture.

    feed() takes the readers' output as it comes and gives back what goes
    on: sound packets are held until the first picture's PTS is known, then
    only those from that PTS on are let through (whole PES packets - one
    sound frame each, FRAME_PES). A stream with no picture, one it cannot
    read, or one whose picture does not come within LEAD_MAX_S passes as
    it is: nothing is ever lost but the sound before the first picture.
    """

    def __init__(self) -> None:
        self.done = False
        #: Seconds of sound left out, once done.
        self.lead = 0.0
        self._rest = b""
        self._pmt: int | None = None
        self._video: int | None = None
        self._sound: set[int] = set()
        self._first: int | None = None      # the first picture's PTS
        self._held: list[tuple[int | None, list[bytes]]] = []
        self._held_pid: dict[int, list[bytes]] = {}
        self._held_bytes = 0
        self._mapped = False
        self._after = 0
        self._dropping: set[int] = set()
        self._passed: set[int] = set()
        self._oldest: int | None = None
        self._cut: int | None = None

    def feed(self, chunk: bytes) -> bytes:
        if self.done:
            return chunk
        data = self._rest + chunk
        out: list[bytes] = []
        at = 0
        while len(data) - at >= _TS:
            pkt = data[at:at + _TS]
            if pkt[0] != 0x47:
                # Not what the readers write: hands off.
                out.append(self._give_up())
                out.append(data[at:])
                self._rest = b""
                return b"".join(out)
            at += _TS
            out.append(self._packet(pkt))
            if self.done:
                out.append(data[at:])
                self._rest = b""
                return b"".join(out)
        self._rest = data[at:]
        return b"".join(out)

    def flush(self) -> bytes:
        """The stream ended: what was still held."""
        if self.done:
            return b""
        rest, self._rest = self._rest, b""
        return self._give_up() + rest

    def _give_up(self) -> bytes:
        held = b"".join(b"".join(pkts) for _pts, pkts in self._held)
        self._held, self._held_pid = [], {}
        self.done = True
        return held

    def _packet(self, pkt: bytes) -> bytes:
        pid = (pkt[1] & 0x1F) << 8 | pkt[2]
        start = bool(pkt[1] & 0x40)
        if pid == 0 and start and self._pmt is None:
            self._read_pat(pkt)
            return pkt
        if pid == self._pmt and start and not self._mapped:
            self._read_pmt(pkt)
            if self._mapped and (self._video is None or not self._sound):
                return self._give_up() + pkt          # sound alone, or none
            return pkt
        if self._first is not None:
            # Bounded: a sound PID that never speaks is not waited for.
            self._after += 1
            if self._after > _AFTER_MAX:
                self._finish()
                return pkt
        if pid == self._video and self._first is None:
            pts = _pes_pts(pkt)
            if pts is None:
                return pkt
            self._first = pts
            return self._release() + pkt
        if pid not in self._sound or pid in self._passed:
            return pkt
        if self._first is None:
            return self._hold(pid, pkt, start)
        if start:
            pts = _pes_pts(pkt)
            if pts is not None and _before(pts, self._first):
                self._dropping.add(pid)
                self._note_cut(pts)
                return b""
            self._dropping.discard(pid)
            self._passed.add(pid)
            if self._passed >= self._sound:
                self._finish()
            return pkt
        return b"" if pid in self._dropping else pkt

    def _hold(self, pid: int, pkt: bytes, start: bool) -> bytes:
        if start:
            pts = _pes_pts(pkt)
            pkts = [pkt]
            self._held.append((pts, pkts))
            self._held_pid[pid] = pkts
            if pts is not None:
                if self._oldest is None:
                    self._oldest = pts
                elif (pts - self._oldest) % _PTS_WRAP > LEAD_MAX_S * 90000:
                    return self._give_up()
        elif pid in self._held_pid:
            self._held_pid[pid].append(pkt)
        else:
            return b""                  # the tail of a PES begun before the piece
        self._held_bytes += _TS
        if self._held_bytes > LEAD_MAX_BYTES:
            return self._give_up()
        return b""

    def _release(self) -> bytes:
        """The first picture is known: the held sound from it on goes out."""
        first = self._first
        assert first is not None
        out: list[bytes] = []
        for pts, pkts in self._held:
            if pts is not None and _before(pts, first):
                self._note_cut(pts)
                continue
            out.extend(pkts)
            self._passed.add((pkts[0][1] & 0x1F) << 8 | pkts[0][2])
        # A sound PES that began before the picture and is still arriving.
        self._dropping = {pid for pid in self._held_pid
                          if pid not in self._passed}
        self._held, self._held_pid = [], {}
        if self._passed >= self._sound:
            self._finish()
        return b"".join(out)

    def _note_cut(self, pts: int) -> None:
        if self._cut is None:
            self._cut = pts

    def _finish(self) -> None:
        if self._cut is not None and self._first is not None:
            self.lead = ((self._first - self._cut) % _PTS_WRAP) / 90000.0
        self.done = True

    def _section(self, pkt: bytes) -> bytes:
        p = _payload(pkt)
        if not p or 1 + p[0] >= len(p):
            return b""
        return p[1 + p[0]:]

    def _read_pat(self, pkt: bytes) -> None:
        s = self._section(pkt)
        if len(s) < 12 or s[0] != 0x00:
            return
        end = min(len(s), 3 + ((s[1] & 0x0F) << 8 | s[2]) - 4)
        for i in range(8, end - 3, 4):
            if s[i] << 8 | s[i + 1]:                    # not the network PID
                self._pmt = (s[i + 2] & 0x1F) << 8 | s[i + 3]
                return

    def _read_pmt(self, pkt: bytes) -> None:
        s = self._section(pkt)
        if len(s) < 16 or s[0] != 0x02:
            return
        end = min(len(s), 3 + ((s[1] & 0x0F) << 8 | s[2]) - 4)
        i = 12 + ((s[10] & 0x0F) << 8 | s[11])
        sound: set[int] = set()
        video: int | None = None
        while i + 5 <= end:
            kind = s[i]
            pid = (s[i + 1] & 0x1F) << 8 | s[i + 2]
            if kind in _PICTURE_TYPES:
                video = pid if video is None else video
            else:
                sound.add(pid)
            i += 5 + ((s[i + 3] & 0x0F) << 8 | s[i + 4])
        self._video, self._sound, self._mapped = video, sound, True


def written(playlist: bytes) -> float:
    """Seconds of media an HLS playlist lists (its EXTINFs)."""
    total = 0.0
    for line in playlist.splitlines():
        if line.startswith(b"#EXTINF:"):
            try:
                total += float(line[8:].split(b",", 1)[0])
            except ValueError:
                continue
    return total


def start_at_beginning(playlist: bytes) -> bytes:
    """An EVENT playlist without its end yet reads as live to a player,
    which starts at its newest segments: the moment asked for is its first
    one. EXT-X-START says so (RFC 8216 4.3.5.2)."""
    if b"#EXT-X-START" in playlist or not playlist.startswith(b"#EXTM3U"):
        return playlist
    head, sep, rest = playlist.partition(b"\n")
    return head + sep + b"#EXT-X-START:TIME-OFFSET=0,PRECISE=YES\n" + rest
