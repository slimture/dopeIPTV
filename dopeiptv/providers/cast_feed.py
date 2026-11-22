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
(-output_ts_offset), so the converter sees one stream. Whatever the piece's
address is - the provider's, or the rewind buffer's loopback server - only
ffmpeg here reads it, and only as an alias through the app's bridge
(core/net_bridge.hand_over): its command line names nothing of the address,
its login, the buffer's token, a proxy or a header that may hold a token.
The TV only ever sees the converter's playlist on the LAN.
"""
from __future__ import annotations

import subprocess
import threading
import time
from typing import IO, Any, Callable
from urllib.parse import urlsplit

from ..core.log import log
from ..core.privacy import url_for_log
from ..core.player_exec import NO_WINDOW, ffmpeg_protocols

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
                net: list[str] | None = None, audio: int = 0, read: str = "") -> list[str]:
    """The command that copies one piece to stdout as MPEG-TS, reporting
    how far it got on stderr (-progress: out_time_us=...). *read*: what it
    reads in *url*'s place - its alias (core/net_bridge.hand_over).

    Only the picture and the one sound track the cast plays (*audio*, what
    -map 0:a:N counts): a second track with no packets in the probe (an
    audio description that is silent for now) has no sample rate, and the
    TS muxer then refuses to write anything at all."""
    read = read or url
    return [
        exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-nostats",
        "-progress", "pipe:2",
        "-analyzeduration", "8000000", "-probesize", "8000000",
        *(net or []),
        # What the piece's address leads to stays a stream's.
        *ffmpeg_protocols(read, bridged=read != url),
        *(["-ss", f"{start:.3f}"] if start > 0 else []),
        *KEYFRAME_INPUT, "-i", read,
        "-map", "0:v:0?", "-map", f"0:a:{max(0, int(audio))}?", "-sn", "-dn",
        "-c", "copy", *KEYFRAME_OUTPUT,
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
        #: The source's own headers for the provider's pieces (Nerd's, as
        #: AirPlay reads them): the bridge adds them (attach).
        self.headers: dict[str, str] = {}
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
               net_for: NetFor | None = None, audio: int = 0,
               headers: dict[str, str] | None = None) -> None:
        """Begin writing the pieces into *sink* (a converter's stdin), on a
        thread of its own; *sink* is closed when they run out. *audio*: the
        one sound track the pieces carry (the converter reads it as 0:a:0).
        *headers*: the source's own, for the provider's pieces - added by
        the app's bridge, never in a reader's arguments but those that open
        nothing (*net_for*'s)."""
        self.detach()
        self.audio = audio
        self.headers = dict(headers or {})
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
                got = self._read(sink, exe, url, at,
                                 media + (JOIN_GAP_S if self.pieces else 0.0),
                                 net_for(url), stop)
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
              stop: threading.Event) -> float | None:
        """Copy one piece into *sink*: how many seconds it gave, or None
        when *sink* is gone - or the piece cannot be handed to a reader
        privately, which ends the stream (nothing is read otherwise)."""
        way = _hand_over(url, self.headers)
        if way is None:
            log.warning("cast feed: the %s cannot be handed to a reader privately - the "
                        "stream ends here", self.kind)
            return None
        try:
            return self._copy(sink, exe, url, at, ts_offset, net, stop, way)
        finally:
            way.close()

    def _copy(self, sink: IO[bytes], exe: str, url: str, at: float,
              ts_offset: float, net: list[str], stop: threading.Event,
              way: Any) -> float | None:
        args = reader_args(exe, url, at, ts_offset, net, self.audio, read=way.address)
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=way.env,
                                    creationflags=NO_WINDOW)
        except OSError as e:
            log.info("cast feed: the reader did not start (%s)", e)
            return 0.0
        with self._lock:
            self._reader = proc
        if stop.is_set():
            _end(proc)
        log.info("cast feed: reading the %s from %s", self.kind,
                 url_for_log(url))
        out: list[float] = [0.0]
        errs = threading.Thread(target=_progress, args=(proc, out), daemon=True)
        errs.start()
        gone = False
        stdout = proc.stdout
        try:
            while stdout is not None:
                chunk = stdout.read(65536)
                if not chunk:
                    break
                try:
                    sink.write(chunk)
                except (OSError, ValueError):
                    gone = True
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


def _hand_over(url: str, headers: dict[str, str]) -> Any:
    """How a reader reads the piece at *url* (core/net_bridge.hand_over):
    the provider's piece as the cast's source's (core/nerd.cast_source), its
    headers added by the bridge; the rewind buffer's own server on this
    computer as no source's, its token kept out of the reader's arguments
    too."""
    from ..core import net_bridge, nerd
    if is_loopback(url):
        return net_bridge.hand_over(None, url)
    return net_bridge.hand_over(nerd.cast_source(), url, headers)


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
