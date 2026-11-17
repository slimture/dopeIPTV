"""AirPlay's bridge: any stream as HLS an Apple TV plays.

An Apple TV fetches what it is handed itself, and it plays HLS and MP4 -
not a continuous MPEG transport stream, not Matroska, not MP2 sound. So
before an address goes to it, the plan decides (plan()):

- Already HLS or MP4 (an Xtream channel's .m3u8, an MP4 film) with codecs
  it takes: handed over as it is, no bridge.
- Anything else: the bundled ffmpeg repackages it here as HLS with
  fragmented MP4 segments and a small HTTP server serves that on the LAN,
  under a path with an unguessable token:

      provider --> ffmpeg (-c copy, 2 s segments) --> HTTP --> Apple TV

H.264 and HEVC pictures and AAC, AC-3 and E-AC-3 sound are copied
untouched, which costs almost nothing. Sound the Apple TV refuses (MP2,
MP3, DTS, LATM...) becomes AAC; a picture it refuses (MPEG-2, MPEG-4 Part
2, VP9...) becomes H.264, which costs the processor and says so. A channel
keeps a rolling live playlist (LIVE_WINDOW_S of it, so a pause holds); a
film keeps every segment and its playlist ends as a VOD one, so the TV's
own remote can scrub it.

The archive and the rewind buffer come in pieces (providers/cast_feed.py):
then ffmpeg reads them one after the other from its stdin, and the playlist
is one EVENT playlist that grows for as long as the cast runs - the Apple
TV plays on from one piece into the next, and can go back within what is
written.

Platform-neutral on purpose: the AVKit side is services/airplay_macos.py,
and everything here is tested on Linux with a stand-in for ffmpeg.
"""
from __future__ import annotations

import collections
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from ..core.log import log
from ..core.player_exec import NO_WINDOW
from ..core.exclusive_bind import ExclusiveBindMixin, IdleTimeoutMixin
from . import cast_feed
from .cast_bridge import (_endless, _hls_file, _hls_name_ok, _timeshift,
                          cast_cache_dir, ffmpeg_path, lan_address,
                          video_encoder)

# What an Apple TV plays out of an HLS stream: these are copied untouched.
COPY_VIDEO = frozenset({"h264", "hevc"})
COPY_AUDIO = frozenset({"aac", "ac3", "eac3"})
# Names mpv and ffmpeg give the same codec, in ffmpeg's words.
_ALIASES = {
    "avc": "h264", "avc1": "h264", "h265": "hevc", "hvc1": "hevc",
    "hev1": "hevc", "mpeg2": "mpeg2video", "ac-3": "ac3", "e-ac-3": "eac3",
    "ec-3": "eac3", "aac-latm": "aac_latm", "latm": "aac_latm",
}
# mpv says which codecs play, not which of them is the picture: known names
# say. One that is neither is assumed fine - converting a picture for no
# reason is the expensive mistake.
_VIDEO = frozenset({
    "h264", "hevc", "mpeg2video", "mpeg1video", "mpeg4", "msmpeg4v3",
    "h263", "vp8", "vp9", "av1", "vc1", "wmv3", "theora", "mjpeg"})
_AUDIO = frozenset({
    "aac", "ac3", "eac3", "mp2", "mp3", "mp1", "aac_latm", "opus", "vorbis",
    "flac", "dts", "truehd", "alac", "wmav2", "amr_nb"})

HLS_PATHS = (".m3u8", ".m3u")
MP4_PATHS = (".mp4", ".m4v", ".mov")

# Seconds per segment - Apple's HLS authoring spec asks for 6. A copied
# picture is cut only at its own keyframes, so with 2 s a provider's
# longer GOP made segments far longer than the playlist's target, and
# the Apple TV gave up (CoreMedia -12888, playlist unchanged too long).
SEGMENT_S = 6
LIVE_WINDOW_S = 300         # how much of a channel the playlist keeps
LEAD = 3                    # segments a live playlist has before it goes
READY_WAIT_S = 30.0         # how long the first playlist may take
PLAYLIST = "index.m3u8"

SOCKETFILTERFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"


def codec_name(codec: str) -> str:
    c = (codec or "").strip().lower()
    return _ALIASES.get(c, c)


def split_codecs(codecs: list[str] | tuple[str, ...]) -> tuple[list[str], list[str]]:
    """(picture codecs, sound codecs) among *codecs*, as ffmpeg names them;
    a name that is neither is left out."""
    video: list[str] = []
    audio: list[str] = []
    for c in codecs or ():
        name = codec_name(c)
        if name in _VIDEO:
            video.append(name)
        elif name in _AUDIO or name.startswith(("pcm_", "dts")):
            audio.append(name)
    return video, audio


def is_loopback(url: str) -> bool:
    """An address on this computer: the rewind buffer's own server, a
    local proxy. An Apple TV can never reach one."""
    return cast_feed.is_loopback(url)


def direct_ok(url: str) -> bool:
    """Whether an Apple TV could fetch *url* itself: http(s), not this
    computer, and HLS or MP4 by its path."""
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")) or is_loopback(u):
        return False
    path = u.split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith(HLS_PATHS + MP4_PATHS)


@dataclass(frozen=True)
class Plan:
    """How a stream reaches the Apple TV."""

    direct: bool            # handed over as it is
    url: str                # direct: what the TV fetches; else what ffmpeg reads
    live: bool = False
    video: str = "copy"     # "copy" or "h264"
    audio: str = "copy"     # "copy" or "aac"
    audio_track: int = 0    # what -map 0:a:N counts
    hevc: bool = False      # copied HEVC: tagged hvc1, or Apple plays nothing
    adts: bool = False      # copied AAC: from a TS it is ADTS, and MP4 wants ASC
    why: str = ""

    @property
    def costs_cpu(self) -> bool:
        return not self.direct and self.video != "copy"


def plan(url: str, source: str = "", codecs: list[str] | tuple[str, ...] = (),
         live: bool = False, audio_track: int = 0,
         own_network: bool = False) -> Plan:
    """The way *url* (what a receiver would be offered - a channel's HLS
    address) or *source* (what the player reads) reaches the Apple TV.

    *codecs*: what is known to play (mpv's names, picture and sound mixed;
    empty when unknown). *own_network*: the source sends a user agent,
    headers or a proxy of its own - which the Apple TV would not, so only
    ffmpeg, which does, may read it."""
    video, audio = split_codecs(codecs)
    v_ok = all(c in COPY_VIDEO for c in video)
    a_ok = all(c in COPY_AUDIO for c in audio)
    if v_ok and a_ok and audio_track == 0 and not own_network:
        for cand in (url, source):
            if cand and direct_ok(cand):
                return Plan(True, cand, live, why="already HLS or MP4")
    reasons = [r for r, bad in (
        (f"picture {','.join(video)} to h264", not v_ok),
        (f"sound {','.join(audio)} to aac", not a_ok),
        (f"sound track {audio_track}", audio_track > 0),
        ("the source's own user agent, headers or proxy", own_network))
        if bad]
    return Plan(False, source or url, live,
                video="copy" if v_ok else "h264",
                audio="copy" if a_ok else "aac",
                audio_track=max(0, int(audio_track)),
                hevc=v_ok and "hevc" in video,
                adts=a_ok and bool(audio) and all(c == "aac" for c in audio),
                why="; ".join(reasons) or "not HLS or MP4")


def refine(p: Plan, probed: dict[str, Any]) -> Plan:
    """*p* with what ffmpeg found in the stream: which codecs to copy."""
    if p.direct:
        return p
    found = [str(probed.get("video") or "")]
    tracks = list(probed.get("audio") or [])
    if 0 <= p.audio_track < len(tracks):
        found.append(str(tracks[p.audio_track]))
    video, audio = split_codecs(found)
    v_ok = all(c in COPY_VIDEO for c in video)
    a_ok = all(c in COPY_AUDIO for c in audio)
    return replace(p, video="copy" if v_ok else "h264",
                   audio="copy" if a_ok else "aac",
                   hevc=v_ok and "hevc" in video,
                   adts=a_ok and audio == ["aac"])


# -- reading the provider -------------------------------------------------------

def input_options(source: str, user_agent: str = "",
                  headers: dict[str, str] | None = None,
                  proxy: str = "") -> list[str]:
    """ffmpeg's input options for *source*: the user agent, headers and
    proxy the source plays with (core/nerd.py), and reconnects for a live
    channel. Only for http(s): ffmpeg refuses an http option on a file or
    a UDP stream before it opens anything. No reconnect for an archive
    window, which a reconnect starts over from its beginning."""
    if not source.lower().startswith(("http://", "https://")):
        return []
    opts: list[str] = []
    if user_agent:
        opts += ["-user_agent", user_agent]
    lines = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items()
                    if k and "\r" not in k + v and "\n" not in k + v)
    if lines:
        opts += ["-headers", lines]
    if proxy:
        opts += ["-http_proxy", proxy]
    if not _timeshift(source):
        opts += ["-reconnect", "1", "-reconnect_streamed", "1",
                 "-reconnect_on_network_error", "1",
                 "-reconnect_delay_max", "5"]
    return opts


def _probe_limits(source: str) -> list[str]:
    # An archive window is joined mid-GOP: the picture's size comes with the
    # next keyframe, which can be past a megabyte (cast_bridge._probe_limits).
    size = "8000000" if _timeshift(source) else "2000000"
    return ["-analyzeduration", size, "-probesize", size]


def endless(source: str) -> bool:
    """A broadcast by its address: a panel's live and archive streams."""
    return _endless(source)


def _clock(text: str) -> float:
    try:
        h, m, s = text.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return 0.0


def parse_probe(text: str) -> dict[str, Any]:
    """What `ffmpeg -i` says about its first input: the picture's codec,
    each sound track's codec, the length in seconds (0 for a broadcast).
    ffmpeg alone, as the Mac app bundles no ffprobe."""
    out: dict[str, Any] = {"video": "", "audio": [], "duration": 0.0}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("Duration:"):
            if not out["duration"]:
                out["duration"] = _clock(line[len("Duration:"):].split(",", 1)[0])
            continue
        if not line.startswith("Stream #0:") or "(attached pic)" in line:
            continue
        for kind in ("Video", "Audio"):
            mark = f": {kind}: "
            if mark not in line:
                continue
            words = line.split(mark, 1)[1].split()
            codec = codec_name(words[0].rstrip(",")) if words else ""
            if kind == "Video" and not out["video"]:
                out["video"] = codec
            elif kind == "Audio":
                out["audio"].append(codec)
    return out


def probe(source: str, net: list[str] | None = None, exe: str | None = None,
          timeout: float = 20.0) -> dict[str, Any]:
    """The stream's codecs and length, from ffmpeg; empty on any failure -
    not knowing is a reason to copy, never to refuse."""
    exe = exe or ffmpeg_path()
    if not exe:
        return parse_probe("")
    try:
        res = subprocess.run(
            [exe, "-hide_banner", "-nostdin", *_probe_limits(source),
             *(net or []), "-i", source],
            capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        found = parse_probe(res.stderr.decode("utf-8", "replace"))
    except Exception as e:
        log.info("airplay: could not look into the stream (%s)", e)
        return parse_probe("")
    log.info("airplay: the stream is %s with %s sound", found["video"] or "?",
             ",".join(found["audio"]) or "no")
    return found


def ffmpeg_args(exe: str, p: Plan, start: float = 0.0,
                net: list[str] | None = None, feed: bool = False) -> list[str]:
    """The command that writes *p* as HLS into the folder it runs in.

    Every name is relative and ffmpeg runs with the folder as its working
    directory, so the playlist, the init segment and the segments land side
    by side whatever this ffmpeg's idea of where an init file goes. The
    segments are fragmented MP4: what HEVC needs on an Apple TV, and what
    carries AC-3 and E-AC-3 there.

    *feed*: the pieces of a broadcast on stdin (cast_feed.Feed), written
    as one EVENT playlist that keeps every segment."""
    if feed:
        src, opts = cast_feed.INPUT, list(cast_feed.INPUT_OPTIONS)
    else:
        src = p.url if "://" in p.url else os.path.abspath(p.url)
        opts = [*_probe_limits(src), *(net or []),
                *(["-ss", f"{start:.3f}"] if start > 0 else [])]
    if p.video == "copy":
        video = ["-c:v", "copy", *(["-tag:v", "hvc1"] if p.hevc else [])]
    else:
        enc = video_encoder()
        video = [
            # Only frames marked interlaced are touched, which a TV channel
            # in MPEG-2 nearly always is.
            "-vf", "yadif=deint=1", "-c:v", enc, "-pix_fmt", "yuv420p",
            *(["-b:v", "6M"] if enc != "libx264" else
              ["-preset", "veryfast", "-crf", "21", "-maxrate", "8M",
               "-bufsize", "16M"]),
            # A keyframe at every segment boundary, or the segments come out
            # as long as the encoder's own idea of a GOP.
            "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_S})"]
    # Copied AAC out of a TS comes as ADTS; the MP4 segments need it as
    # ASC, or ffmpeg stops at the first packet ("Malformed AAC bitstream").
    audio = (["-c:a", "copy", *(["-bsf:a", "aac_adtstoasc"] if p.adts else [])]
             if p.audio == "copy" else
             ["-c:a", "aac", "-ac", "2", "-b:a", "192k"])
    keep = (["-hls_list_size", str(LIVE_WINDOW_S // SEGMENT_S),
             "-hls_flags", "delete_segments+temp_file"] if p.live and not feed
            else
            # A film keeps every segment; ENDLIST at its end makes the
            # playlist a whole one (served as VOD, see serve_playlist).
            ["-hls_list_size", "0", "-hls_playlist_type", "event",
             "-hls_flags", "temp_file"])
    return [
        exe, "-hide_banner", "-loglevel", "error",
        *([] if feed else ["-nostdin"]), *opts,
        "-fflags", "+genpts", "-i", src,
        # A feed carries only the chosen track (cast_feed.reader_args).
        "-map", "0:v:0?", "-map", f"0:a:{0 if feed else p.audio_track}?", "-sn", "-dn",
        *video, *audio, "-max_muxing_queue_size", "1024",
        "-f", "hls", "-hls_time", str(SEGMENT_S),
        "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
        *keep, "-hls_segment_filename", "s%05d.m4s", PLAYLIST,
    ]


def serve_playlist(body: bytes, from_start: bool = False) -> bytes:
    """A playlist as it goes out: a film's, once ffmpeg has finished it,
    as the VOD playlist it now is; a broadcast's pieces (*from_start*)
    played from their first segment, not from the newest."""
    if from_start:
        body = cast_feed.start_at_beginning(body)
    if b"#EXT-X-ENDLIST" in body:
        return body.replace(b"#EXT-X-PLAYLIST-TYPE:EVENT",
                            b"#EXT-X-PLAYLIST-TYPE:VOD")
    return body


def segments_in(playlist: bytes) -> int:
    return sum(1 for line in playlist.splitlines()
               if line.strip() and not line.startswith(b"#"))


# -- the firewall --------------------------------------------------------------

def parse_firewall(state: str, block_all: str, app: str) -> bool:
    """Whether socketfilterfw's answers say macOS refuses incoming
    connections for this app: the firewall on, and every connection
    blocked or this app named as blocked. Not yet asked is not refused -
    macOS asks the user then."""
    s = state.lower()
    if "disabled" in s or "state = 0" in s or "enabled" not in s:
        return False
    ba = block_all.lower()
    if "state = 2" in s or ("enabled" in ba and "disabled" not in ba):
        return True
    a = app.lower()
    return "block" in a and not any(w in a for w in ("permit", "allow", "unblock"))


def app_path() -> str:
    """This app as the firewall names it: the .app bundle when frozen."""
    exe = sys.executable
    marker = ".app/Contents/MacOS/"
    if marker in exe:
        return exe.split(marker, 1)[0] + ".app"
    return exe


def firewall_blocks(run: Callable[..., Any] = subprocess.run) -> bool:
    """macOS's application firewall would turn the Apple TV away. False
    off macOS and whenever it cannot be told."""
    if sys.platform != "darwin" or not os.access(SOCKETFILTERFW, os.X_OK):
        return False

    def ask(*args: str) -> str:
        try:
            res = run([SOCKETFILTERFW, *args], capture_output=True,
                      timeout=5)
            return (res.stdout or b"").decode("utf-8", "replace")
        except Exception:
            return ""

    blocked = parse_firewall(ask("--getglobalstate"), ask("--getblockall"),
                             ask("--getappblocked", app_path()))
    if blocked:
        log.info("airplay: the macOS firewall refuses incoming connections "
                 "for this app")
    return blocked


# -- serving -------------------------------------------------------------------

_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".m4s": "video/iso.segment",
          ".mp4": "video/mp4"}


class _Server(ExclusiveBindMixin, ThreadingHTTPServer):
    daemon_threads = True
    bridge: "AirPlayBridge"

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError,
                            TimeoutError, ConnectionAbortedError)):
            return                      # the receiver hung up
        log.info("airplay bridge: a request failed (%s)", exc)


class _Handler(IdleTimeoutMixin, BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass                            # the app has its own log

    def do_HEAD(self) -> None:
        self._answer(body=False)

    def do_GET(self) -> None:
        self._answer(body=True)

    def _answer(self, body: bool) -> None:
        """One file of the HLS set - only under the run's token, only a
        plain name, only one found in the run's own folder (never a path
        built from the request, as in cast_bridge._hls_file)."""
        bridge = self.server.bridge
        path = self.path.split("?", 1)[0]
        prefix = bridge.prefix
        if not prefix or not secrets.compare_digest(
                path[:len(prefix)].encode(), prefix.encode()):
            self.send_error(404)
            return
        name = path[len(prefix):]
        if not _hls_name_ok(name):
            self.send_error(404)
            return
        bridge.saw(str(self.client_address[0]))
        file = _hls_file(bridge.folder, name)
        if file is None:
            self.send_error(404)
            return
        try:
            with open(file, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        ext = os.path.splitext(name)[1]
        if ext == ".m3u8":
            data = serve_playlist(data, bridge.feed is not None)
        self.send_response(200)
        self.send_header("Content-Type",
                         _TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        if ext == ".m3u8":
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if body:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass


class AirPlayBridge:
    """One stream as HLS on the LAN for as long as an AirPlay cast runs."""

    def __init__(self) -> None:
        self.exe: str | None = None          # a stand-in, for tests
        self.prefix: str | None = None
        self.folder: str | None = None
        self.url: str = ""
        self.plan: Plan | None = None
        self.error = ""
        # Whether anything but this computer asked for the stream: the
        # Apple TV got through (the firewall let it).
        self.remote_seen = False
        # How much room a pause may take - set by the cast strip; the live
        # window (LIVE_WINDOW_S) is what bounds it here.
        self.cap = 0
        #: The pieces ffmpeg reads (the archive, the rewind buffer), if any.
        self.feed: cast_feed.Feed | None = None
        self._own: set[str] = set()
        self._server: _Server | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._tail: collections.deque[str] = collections.deque(maxlen=8)
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        return ffmpeg_path() is not None

    @property
    def hls(self) -> bool:
        """Serving a playlist now - what the strip asks to know whether the
        TV can move within it by itself."""
        return self._server is not None

    def running(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def start(self, p: Plan, start: float = 0.0,
              net: list[str] | None = None,
              feed: cast_feed.Feed | None = None,
              net_for: cast_feed.NetFor | None = None) -> str:
        """Begin converting *p* and serving it; returns the playlist's
        address on the LAN. ffmpeg starts at once: by the time someone has
        picked their Apple TV, the first segments are there. With *feed*,
        ffmpeg reads its pieces (each read with *net_for*'s options); the
        bridge owns it from now and closes it when another stream replaces
        it or the bridge stops."""
        self._stop(keep=feed)
        exe = self.exe or ffmpeg_path()
        if not exe:
            raise RuntimeError("no ffmpeg")
        self.plan, self.error, self.remote_seen = p, "", False
        self._tail.clear()
        self.folder = tempfile.mkdtemp(prefix="airplay-", dir=_cache_dir())
        self.prefix = f"/{secrets.token_urlsafe(18)}/"
        server = _Server(("0.0.0.0", 0), _Handler)
        server.bridge = self
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.2}, daemon=True).start()
        self._server = server
        host = lan_address()
        self._own = {"127.0.0.1", "::1", "::ffff:127.0.0.1", host,
                     f"::ffff:{host}"}
        self.url = f"http://{host}:{server.server_address[1]}{self.prefix}{PLAYLIST}"
        args = ffmpeg_args(exe, p, start, net, feed=feed is not None)
        try:
            self._proc = subprocess.Popen(
                args, cwd=self.folder,
                stdin=subprocess.PIPE if feed is not None else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=NO_WINDOW)
        except OSError as e:
            self.stop()
            raise RuntimeError(f"ffmpeg did not start ({e})") from e
        if feed is not None and self._proc.stdin is not None:
            self.feed = feed
            feed.attach(self._proc.stdin, exe, net_for, audio=p.audio_track)
        # Its pid beside its files: a run that crashes leaves ffmpeg pulling
        # the channel - one of the account's connections - and the next
        # start ends it (sweep_leftovers).
        try:
            with open(os.path.join(self.folder, PIDFILE), "w") as fh:
                fh.write(str(self._proc.pid))
        except OSError:
            pass
        threading.Thread(target=self._drain, args=(self._proc,),
                         daemon=True).start()
        log.info("airplay bridge: serving on port %d (%s; picture %s, sound "
                 "%s, %s)", server.server_address[1], p.why,
                 p.video, p.audio,
                 f"the {feed.kind} in pieces, one playlist" if feed is not None
                 else "live" if p.live else "film")
        if p.costs_cpu:
            log.info("airplay bridge: the picture is converted to H.264 "
                     "here (%s) - this costs the processor", video_encoder())
        return self.url

    def _drain(self, proc: subprocess.Popen[bytes]) -> None:
        last, said = "", 0
        try:
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line or line == last:
                    continue
                last = line
                self._tail.append(line)
                said += 1
                if said <= 20:
                    log.info("airplay bridge: ffmpeg: %s", line)
        except Exception:
            pass

    def _playlist(self) -> bytes:
        file = _hls_file(self.folder, PLAYLIST)
        if file is None:
            return b""
        try:
            with open(file, "rb") as f:
                return f.read()
        except OSError:
            return b""

    def written(self) -> float:
        """Seconds the playlist holds - how far the TV can move in it."""
        return cast_feed.written(self._playlist())

    def ready(self) -> bool:
        """The playlist has enough to start: LEAD segments of a channel (an
        Apple TV starts three segments from the live edge), one of a film or
        of pieces read here (a moment gone back to: the playlist says to
        start at its first segment, EXT-X-START, so there is no live edge to
        keep back from) - or all there is of something shorter."""
        body = self._playlist()
        n = segments_in(body)
        if b"#EXT-X-ENDLIST" in body:
            return n > 0
        live = self.plan is not None and self.plan.live and self.feed is None
        return n >= (LEAD if live else 1)

    def wait_ready(self, timeout: float = READY_WAIT_S,
                   stopping: Callable[[], bool] | None = None) -> bool:
        """Wait for ready(); False when ffmpeg gave up (self.error says
        why), the time ran out or *stopping* says the cast went away."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.ready():
                return True
            proc = self._proc
            if proc is None or (stopping is not None and stopping()):
                return False
            if proc.poll() is not None:
                time.sleep(0.2)                 # the last lines of stderr
                if self.ready():
                    return True
                self.error = self.error or (self._tail[-1] if self._tail else
                                            f"ffmpeg exited ({proc.returncode})")
                return False
            time.sleep(0.1)
        self.error = self.error or "no stream after %d s" % timeout
        return False

    def saw(self, addr: str) -> None:
        if addr not in self._own and not self.remote_seen:
            self.remote_seen = True
            log.info("airplay bridge: the receiver is reading the stream")

    def stop(self) -> None:
        self._stop()

    def _stop(self, keep: cast_feed.Feed | None = None) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            server, self._server = self._server, None
            folder, self.folder = self.folder, None
            feed, self.feed = self.feed, None
        if feed is not None:
            # Done with, unless it is the one being started again.
            feed.detach() if feed is keep else feed.close()
        if proc is not None:
            for step, grace in ((proc.terminate, 1.0), (proc.kill, 0.5)):
                try:
                    step()
                    proc.wait(timeout=grace)
                    break
                except Exception:
                    continue
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
            log.info("airplay bridge: stopped")
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        self.prefix, self.url = None, ""


PIDFILE = "ffmpeg.pid"


def _is_ffmpeg(pid: int, run: Callable[..., Any] = subprocess.run) -> bool:
    """Whether process *pid* is an ffmpeg (a pid can have been reused)."""
    try:
        if sys.platform == "win32":
            out = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                      capture_output=True, text=True, timeout=3,
                      creationflags=NO_WINDOW).stdout
        else:
            out = run(["ps", "-p", str(pid), "-o", "comm="],
                      capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "ffmpeg" in (out or "").lower()


def _end(pid: int, run: Callable[..., Any] = subprocess.run) -> None:
    try:
        if sys.platform == "win32":
            run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                timeout=3, creationflags=NO_WINDOW)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def sweep_leftovers(base: str | None = None,
                    run: Callable[..., Any] = subprocess.run) -> int:
    """End the ffmpeg of an AirPlay run that could not tidy up (a crash, a
    kill) and remove its files; how many ffmpeg were ended. Such an ffmpeg
    goes on pulling the channel, which takes one of the account's
    connections from the player."""
    ended = 0
    try:
        base = base or cast_cache_dir()
        names = [n for n in os.listdir(base) if n.startswith("airplay-")]
    except OSError:
        return 0
    for name in names:
        folder = os.path.join(base, name)
        try:
            with open(os.path.join(folder, PIDFILE)) as fh:
                pid = int(fh.read().strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if pid > 0 and pid != os.getpid() and _is_ffmpeg(pid, run):
            _end(pid, run)
            ended += 1
            log.info("airplay bridge: ended an ffmpeg a run before left")
        shutil.rmtree(folder, ignore_errors=True)
    return ended


def _cache_dir() -> str:
    """The cast cache folder, swept of what an AirPlay run that could not
    tidy up (a crash, a kill) left behind."""
    base = cast_cache_dir()
    sweep_leftovers(base)
    return base
