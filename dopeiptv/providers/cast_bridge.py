"""A local bridge that makes a stream castable when the receiver refuses it.

Some channels are perfectly ordinary H.264 video with Dolby Digital Plus
audio. mpv plays them without blinking; a Chromecast that is not an Ultra or
a Google TV has no E-AC-3 decoder at all and answers IDLE/ERROR without ever
saying why. No address, no MIME type and no retry can change that - the only
way such a channel reaches the TV is to hand it something it can decode.

So this runs ffmpeg here and serves the result on the LAN:

    provider --> ffmpeg (audio to AAC, video copied) --> HTTP --> Chromecast

Copying the video is the whole trick. Re-encoding 1080p live would cook the
machine; copying it costs almost nothing, and it is only the audio that the
receiver could not decode. Video is only ever re-encoded when it too is
something the receiver cannot take (HEVC), and that case is expensive and
says so in the log.

The bridge is started only after a direct cast has already been refused, it
serves exactly one stream at a time, and stopping the cast stops both the
server and ffmpeg.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..core.log import log

# What a Cast receiver decodes. Anything outside these has to be re-encoded;
# anything inside is copied through untouched.
SAFE_VIDEO = {"h264", "avc1", "vp8"}
SAFE_AUDIO = {"aac", "aac-latm", "mp3", "vorbis", "opus"}

# A player User-Agent: panels routinely refuse anything that looks automated.
_UA = "VLC/3.0.20 LibVLC/3.0.20"


# What each generation of receiver can take, as (max lines, max frames a
# second). Zero means no limit. Deinterlacing is not part of the choice: no
# Chromecast deinterlaces at all, so it is applied whenever the video is
# re-encoded anyway - with deint=1, which touches only frames actually
# marked as interlaced.
#
# Named after what the device IS, because nobody knows their receiver's
# maximum profile level and everybody knows which one they bought:
#
#   Chromecast 1 (2013)                 1080p30   H.264 up to L4.1
#   Chromecast 2, 3, Audio (2015-18)    1080p60
#   Chromecast with Google TV HD (2022) 1080p60
#   Ultra (2016), Google TV 4K (2020),
#   Google TV Streamer (2024)           2160p60   HEVC/VP9, HDR
#
# The limit is the decoder and the size, not the bitrate - a 4K stream is
# not slow on a first-generation dongle, it is undecodable.
QUALITY = {
    "original": (0, 0),        # newest: 4K and 60 fps, nothing adapted
    "hd": (1080, 0),           # ordinary: 4K comes down to 1080, frame rate
                               # left alone, because 1080p60 is within it
    "oldest": (1080, 30),      # first generation: 1080p30 is its ceiling
}

def normalise_quality(value: str | None) -> str:
    """A stored setting, in today's terms.

    Devices have been written down as "720p", "720p30" and "older" as this
    question changed shape. Every one of those was somebody saying "this is
    an old receiver", so they all mean the oldest tier now - which is
    kinder to them than they were: it caps a 4K film at 1080p30 instead of
    720p, and a first-generation dongle decodes 1080p30 perfectly well.
    """
    if not value or value == "original":
        return "original"
    if value in QUALITY:
        return value
    return "oldest"

# What ffmpeg says while it waits for the first keyframe of a broadcast that
# was already running. None of it is a fault: a transport stream can be joined
# at any byte, and everything before the next keyframe is genuinely
# undecodable. It stops on its own within a second or two.
_MID_STREAM_NOISE = (
    "non-existing pps", "no frame!", "last message repeated",
    "sps unavailable", "decode_slice_header error",
    "error while decoding mb", "missing picture in access unit",
    "co located pocs unavailable", "corrupt decoded frame",
    "invalid nal unit size", "concealing",
)


def _mid_stream_noise(line: str) -> bool:
    low = line.lower()
    return any(bit in low for bit in _MID_STREAM_NOISE)


# ffmpeg failures that will say exactly the same thing next time. The retry
# is there for a provider still counting a connection we closed a moment ago,
# which frees up within seconds - a filter this build does not have never
# will, and trying twice more only spends twenty seconds proving it.
_FATAL = ("no such filter", "unknown encoder", "unknown decoder",
          "error opening output file", "option not found",
          "invalid argument", "unrecognized option")


def _fatal_error(line: str) -> bool:
    low = line.lower()
    return any(bit in low for bit in _FATAL)


_hw_encoder: str | None = None
_ffmpeg: str | None | bool = False       # False = not looked for yet


def _ffmpeg_candidates() -> list[str]:
    """Every ffmpeg on this machine worth considering, best guess first.

    The one shipped inside a frozen build comes first because it is the one
    the app was tested with, then whatever PATH says, then the places package
    managers actually put things - a Homebrew ffmpeg is invisible to a bundle
    launched from Finder, whose PATH is the bare system one.
    """
    found: list[str] = []
    for cand in (_bundled(), shutil.which("ffmpeg"),
                 "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                 "/opt/local/bin/ffmpeg", "/usr/bin/ffmpeg",
                 "/snap/bin/ffmpeg", "/var/lib/flatpak/exports/bin/ffmpeg"):
        if cand and cand not in found and os.access(cand, os.X_OK):
            found.append(cand)
    return found


def _bundled() -> str | None:
    """An ffmpeg shipped inside a frozen build, which is not on PATH."""
    if not getattr(sys, "frozen", False):
        return None
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    for cand in (os.path.join(base, exe),
                 os.path.join(os.path.dirname(sys.executable), exe)):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def video_encoder() -> str:
    """The h264 encoder to use - the hardware one where there is one.

    Scaling a 1080i50 channel down in software is the kind of thing that
    makes a laptop's fans audible; VideoToolbox does it on the media engine
    for almost nothing. Asked once, since it cannot change while running.
    """
    global _hw_encoder
    if _hw_encoder is None:
        _hw_encoder = "libx264"
        exe = ffmpeg_path()
        if exe:
            try:
                out = subprocess.run([exe, "-hide_banner", "-encoders"],
                                     capture_output=True, timeout=10).stdout
                if b"h264_videotoolbox" in out:
                    _hw_encoder = "h264_videotoolbox"
            except Exception:
                pass
        log.info("cast bridge: video encoder is %s", _hw_encoder)
    return _hw_encoder


def cast_cache_dir() -> str:
    """Where a cast's working files live, swept clean of older runs.

    The app's own cache directory, so it is on disk (a Linux /tmp is often
    tmpfs, which is RAM, and a paused broadcast runs to gigabytes) and so a
    user clearing the cache picks it up. Anything left behind by a run that
    did not get to tidy up - a crash, a kill - goes on the way in, because
    nobody wants to discover four gigabytes of a fortnight-old pause.
    """
    try:
        from ..core.workers import default_image_cache_dir
        base = default_image_cache_dir("cast")
    except Exception:
        base = Path.home() / ".cache" / "dopeiptv" / "cast"
    base.mkdir(parents=True, exist_ok=True)
    for stale in base.glob("cast-*"):
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
    return str(base)


def ffmpeg_path() -> str | None:
    """Which ffmpeg to run, or None when there is none.

    The first one found, in the order candidates are looked for. There used
    to be a preference for a build with libass, because a text subtitle had
    to be drawn into the picture and only libass could do it - that is gone.
    A text subtitle now travels beside the picture as WebVTT, which any
    ffmpeg can write, so no build is better than another for this.

    Asked once: it cannot change while the app is running.
    """
    global _ffmpeg
    if _ffmpeg is not False:
        return _ffmpeg                   # type: ignore[return-value]
    found = _ffmpeg_candidates()
    _ffmpeg = found[0] if found else None
    if _ffmpeg is None:
        log.info("cast bridge: no ffmpeg found - streams the receiver "
                 "refuses cannot be converted")
    else:
        log.info("cast bridge: using %s", _ffmpeg)
    return _ffmpeg                       # type: ignore[return-value]


def _ffprobe_path() -> str:
    """The ffprobe belonging to the ffmpeg we settled on."""
    ff = ffmpeg_path()
    if ff:
        cand = os.path.join(os.path.dirname(ff),
                            "ffprobe.exe" if sys.platform == "win32"
                            else "ffprobe")
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which("ffprobe") or ""


def lan_address() -> str:
    """This machine's address on the LAN, as the Chromecast will see it.

    A UDP socket is 'connected' to an outside address to find out which
    interface the kernel would route through - nothing is sent, and it is the
    only way that survives a machine with several interfaces (Wi-Fi, Ethernet,
    a VPN, Docker) where the hostname resolves to the wrong one.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# Subtitle codecs that are pictures rather than text. They are laid over the
# video with the overlay filter; text ones go through the subtitles filter,
# which has to re-read the source and therefore needs its path escaped.
BITMAP_SUBS = {"dvb_subtitle", "dvd_subtitle", "hdmv_pgs_subtitle", "xsub"}


def probe_tracks(source: str, exe: str | None = None) -> dict:
    """List the audio and subtitle tracks in *source* with ffprobe.

    Returns {"audio": [...], "subtitle": [...]}, each entry carrying the index
    within its own kind (which is what -map 0:a:N counts), the codec, the
    language and any title. Empty on any failure: not being able to list the
    tracks is a reason to offer no choice, never a reason to block a cast.
    """
    # The ffprobe next to the ffmpeg we chose, so both come from the same
    # build - a Homebrew ffmpeg with a system ffprobe would report on one
    # thing and convert with another.
    exe = exe or _ffprobe_path()
    out: dict = {"audio": [], "subtitle": [], "duration": 0.0,
                 "height": 0, "fps": 0.0}
    if not exe:
        return out
    try:
        raw = subprocess.run(
            [exe, "-v", "error", *(["-user_agent", _UA]
                                   if "://" in source else []),
             "-print_format", "json", "-show_streams", "-show_format",
             source],
            capture_output=True, timeout=25).stdout
        probed = json.loads(raw or b"{}")
        streams = probed.get("streams", [])
        # The runtime comes along for the ride: a resume point is only worth
        # storing against a known length, and the receiver cannot report one
        # for a converted stream - it arrives down a pipe with no end in it.
        try:
            out["duration"] = float((probed.get("format") or {})
                                    .get("duration") or 0.0)
        except (TypeError, ValueError):
            out["duration"] = 0.0
    except Exception as e:
        log.info("cast bridge: could not list the tracks (%s)", e)
        return out
    for s in streams:
        kind = s.get("codec_type")
        if kind == "video" and not out["height"]:
            # What the picture actually is, so an adaptation meant for a
            # 1080-line channel is not applied to the HD version of the same
            # channel, which the receiver plays perfectly well.
            out["height"] = int(s.get("height") or 0)
            try:
                num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
                out["fps"] = round(float(num) / float(den or 1), 3)
            except (TypeError, ValueError, ZeroDivisionError):
                out["fps"] = 0.0
        if kind not in out or not isinstance(out.get(kind), list):
            continue
        tags = s.get("tags") or {}
        out[kind].append({
            "index": len(out[kind]),          # what -map 0:a:N / 0:s:N counts
            "codec": s.get("codec_name") or "?",
            "lang": (tags.get("language") or "").strip(),
            "title": (tags.get("title") or "").strip(),
            "default": bool((s.get("disposition") or {}).get("default")),
        })
    log.info("cast bridge: %d audio track(s), %d subtitle track(s)",
             len(out["audio"]), len(out["subtitle"]))
    return out


def _input_options(source: str) -> list[str]:
    """Input options for *source* - only the ones its protocol accepts.

    A player User-Agent and the reconnect flags belong to ffmpeg's HTTP
    reader. Passing them for a local file is not merely useless: ffmpeg exits
    with "Option user_agent not found" before it opens anything, which is
    every recording on disk failing to cast.

    The reconnects matter for the streams that do use them: an IPTV panel
    drops HTTP connections between segments as a matter of course, and left
    alone ffmpeg eventually gives up on one and the cast dies mid-programme.

    Reconnecting at EOF is deliberately NOT among them. These panels cut an
    archive stream short - announcing ten megabytes and delivering one - and
    a reconnect on a stream nobody can seek starts it again from the
    beginning rather than continuing. The picture went back to where it had
    started, over and over, which is worse than stopping: at least stopping
    says something is wrong.
    """
    if "://" not in source:
        return []
    if _timeshift(source):
        # No reconnects for an archive window. The panel closes the stream
        # at its write head - that is the END of the stretch, and the app
        # asks for the next one from that exact moment. A reconnect instead
        # re-requests the same window, which on a panel that ignores Range
        # starts it over from the beginning: television replayed at random,
        # mid-programme, which is what "it keeps restarting" was.
        return ["-user_agent", _UA]
    return ["-user_agent", _UA,
            "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1", "-reconnect_delay_max", "5"]


def _probe_limits(source: str) -> list[str]:
    """How much ffmpeg may read before it decides what the stream contains.

    A megabyte for a live channel: the default is five megabytes or five
    seconds, whichever comes first, and over a provider link that is most
    of the wait before a picture appears. A transport stream announces
    itself in its first PMT, so a megabyte is plenty for one that is
    already running.

    An archive stretch is a different matter. It is joined at whatever byte
    the panel starts sending, which is the middle of a GOP - there is no
    SPS until the next keyframe, and at 720p50 that can be past a
    megabyte. ffmpeg then never learns the picture size, and the mp4 muxer
    refuses to write a header without it:

        [mp4 @ ...] dimensions not set
        Could not write header (incorrect codec parameters ?): Invalid
        argument
        Nothing was written into output file

    - and "invalid argument" counts as fatal, so it did not even try
    again. Which is exactly what winding a timeshift channel back did.
    """
    if _timeshift(source):
        return ["-analyzeduration", "8000000", "-probesize", "8000000"]
    return ["-analyzeduration", "1000000", "-probesize", "1000000"]


def _endless(source: str) -> bool:
    """Whether this is a broadcast rather than something with an end.

    Decides how much of an HLS set is kept: a channel rolls a short window
    and deletes behind itself, a film keeps every segment so the
    television's own remote can scrub it. Read off the address because that
    is what the bridge is given - a panel's live and catch-up streams both
    say so in their path, and everything else is a file with a length.
    """
    path = source.split("?", 1)[0].lower()
    return "/live/" in path or _timeshift(source)


def _timeshift(source: str) -> bool:
    path = source.split("?", 1)[0].lower()
    return "/timeshift/" in path or "timeshift.php" in path


def _filter_escape(source: str) -> str:
    """Escape a source for use as a filter option value.

    A filtergraph is parsed twice: the description splits on , ; [ ] and
    honours \\ and quotes, then each filter splits its own arguments on :.
    A colon therefore has to survive both, which takes a DOUBLED backslash -
    the description eats one and the option parser sees the other. This is
    the form ffmpeg's own documentation uses for a Windows drive letter.

    Quoting instead of escaping is what broke it: newer ffmpeg takes a
    backslash inside quotes literally, so 'http\\://host\\:2095/x.mkv' became
    a filename with backslashes in it and the parse failed on the leftovers -

        No option name near 'http\\://lol.bz\\:2095/movie/....mkv:si=4'

    - while older builds accepted the very same string. Unquoted and doubled
    is accepted by both.
    """
    out = source.replace("\\", "\\\\\\\\")
    for ch in (":", ",", ";", "[", "]", "'"):
        out = out.replace(ch, "\\\\" + ch)
    return out


def ffmpeg_args(exe: str, source: str, copy_video: bool,
                audio: int = 0, subs: int | None = None,
                sub_codec: str = "", start: float = 0.0,
                quality: str = "original") -> list[str]:
    """The command line, kept separate so it can be read and tested.

    Fragmented MP4 down a single HTTP response: the receiver is a Chrome
    engine and plays it as it arrives, and unlike HLS there are no segment
    files to write, name, serve and clean up.

    A chosen subtitle is burned into the picture. The receiver only renders
    subtitles it was handed as a separate WebVTT file, which cannot be made
    from a live stream - nothing carried inside the stream is ever offered to
    it. Burning them in always works, at the cost of re-encoding the video,
    which is why it happens only when a subtitle is actually chosen.
    """
    height, fps = QUALITY.get(quality, (0, 0))
    if height or fps:
        copy_video = False                    # nothing to adapt in a copy
    chain: list[str] = []
    burn: list[str] = []
    if subs is not None:
        copy_video = False                    # a picture that changes must be
        if sub_codec in BITMAP_SUBS:          # re-encoded, whatever it held
            burn = ["-filter_complex",
                    f"[0:v:0][0:s:{subs}]overlay[v]", "-map", "[v]"]
        else:
            # A text subtitle never reaches this branch any more: it goes to
            # the receiver as a WebVTT rendition alongside the picture (see
            # hls_args), which is how everything that streams does it. The
            # subtitles filter built the WHOLE track before drawing a line -
            # measured, it read 100% of the file before the first frame - so
            # burning one in from a provider link meant fetching the film
            # first. Only a picture-based subtitle is drawn here now.
            burn = ["-map", "0:v:0"]
            burn = ["-map", "0:v:0"]
    else:
        burn = ["-map", "0:v:0"]
    if not copy_video:
        # Only frames flagged interlaced are touched, so this is free on
        # progressive video and the difference between a combed, stuttering
        # picture and a clean one on everything a TV channel actually sends.
        chain.insert(0, "yadif=deint=1")
    if height:
        chain.append(f"scale=-2:{height}")
    if chain and "-filter_complex" not in burn:
        burn = ["-vf", ",".join(chain)] + burn
    return [
        exe, "-hide_banner", "-loglevel", "error",
        # How much of the stream ffmpeg reads before it will emit anything.
        # Left alone it takes up to five megabytes or five seconds of it,
        # whichever comes first - which over a provider link is most of the
        # wait before a picture appears, and it was measured at thirteen
        # seconds for one and a half megabytes. A transport stream announces
        # what it carries in its first PMT, so a megabyte is plenty.
        *_probe_limits(source),
        *_input_options(source),
        # Seeking before -i is the cheap kind: ffmpeg jumps in the container
        # instead of decoding its way there, which is what makes resuming an
        # hour into a film instant rather than a minute of waiting.
        *(["-ss", f"{start:.3f}"] if start > 0 else []),
        "-fflags", "+genpts", "-i", source,
        *burn, "-map", f"0:a:{audio}",
        *(["-r", str(fps)] if fps else []),
        "-c:v", "copy" if copy_video else video_encoder(),
        *([] if copy_video else
          (["-b:v", "4M"] if video_encoder() != "libx264" else
           ["-preset", "veryfast", "-crf", "23",
            "-maxrate", "6M", "-bufsize", "12M"])),
        "-c:a", "aac", "-ac", "2", "-b:a", "192k",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "pipe:1",
    ]


HLS_SEGMENT = 4          # seconds per segment


def hls_args(exe: str, source: str, copy_video: bool, folder: str,
             audio: int = 0, subs: int | None = None, start: float = 0.0,
             quality: str = "original", live: bool = False) -> list[str]:
    """The command for a cast that carries a text subtitle.

    Not burned into the picture: handed over beside it, as a WebVTT
    rendition in an HLS playlist, which is how everything that streams does
    subtitles and what the receiver renders natively.

    The reason is measured. ffmpeg's subtitles filter builds the WHOLE
    subtitle track before it draws a single line - on a counting server its
    two opens of the source had read 100% of the file when the first frame
    came out, while the picture's own read had managed 55%. Burning a
    subtitle into a film coming down a provider link therefore meant
    fetching the entire film first, and the television sat black through it.

    As a plain stream copy there is nothing to preload: the same measurement
    against a source fed at a thirtieth of real speed had the first WebVTT
    segment written after 0.1 seconds and 3% of the file. It also costs the
    account exactly one connection, because nothing opens the source twice.

    The picture is copied through where the receiver can take it, exactly as
    it is for a cast without subtitles - a subtitle is no longer a reason to
    re-encode anything.
    """
    height, fps = QUALITY.get(quality, (0, 0))
    if height or fps:
        copy_video = False
    chain: list[str] = []
    if not copy_video:
        chain.append("yadif=deint=1")
    if height:
        chain.append(f"scale=-2:{height}")
    return [
        exe, "-hide_banner", "-loglevel", "error",
        *_probe_limits(source),
        *_input_options(source),
        *(["-ss", f"{start:.3f}"] if start > 0 else []),
        "-fflags", "+genpts", "-i", source,
        "-map", "0:v:0", "-map", f"0:a:{audio}", "-map", f"0:s:{subs}",
        *(["-vf", ",".join(chain)] if chain else []),
        *(["-r", str(fps)] if fps else []),
        "-c:v", "copy" if copy_video else video_encoder(),
        *([] if copy_video else
          (["-b:v", "4M"] if video_encoder() != "libx264" else
           ["-preset", "veryfast", "-crf", "23",
            "-maxrate", "6M", "-bufsize", "12M"])),
        "-c:a", "aac", "-ac", "2", "-b:a", "192k",
        # WebVTT is the one subtitle format a Cast receiver renders.
        "-c:s", "webvtt",
        "-f", "hls",
        "-hls_time", str(HLS_SEGMENT),
        # A broadcast keeps a rolling window; a film keeps everything, which
        # is what lets the television's own remote scrub it - and what makes
        # a pause cost nothing, because the segments go on being written
        # while the receiver sits still.
        # independent_segments was tried here and taken out again. It made
        # no difference to the wait before the sound, and everything got
        # slower and stalled more with it in - which may or may not have
        # been its doing, and that is exactly the reason not to keep an
        # unproven flag in the one path that works.
        *(["-hls_list_size", "6", "-hls_flags", "delete_segments+temp_file"]
          if live else
          ["-hls_list_size", "0", "-hls_playlist_type", "event",
           "-hls_flags", "temp_file"]),
        # temp_file above matters more than it looks: without it a playlist
        # is served half-written to a receiver that asked at the wrong
        # moment, and the cast dies on a parse error nobody can see.
        "-hls_segment_filename", os.path.join(folder, "v%d.ts"),
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", "v:0,a:0,s:0,sgroup:subs",
        os.path.join(folder, "stream_%v.m3u8"),
    ]


class _Spool:
    """The recording behind a cast, in pieces, so what has been watched can
    be thrown away while the rest keeps arriving.

    One file would have answered just as well until the arithmetic was done:
    it grows for the whole sending, not just for a pause, and a football
    match is three hours. What actually has to be kept is the stretch between
    the slowest reader and the write head - seconds of it while the
    television is playing, and exactly the length of a pause while it is not.
    Pieces make that possible: finish one, and once everybody is past it, it
    is deleted.
    """

    PIECE = 16_000_000          # a few seconds of a copied broadcast

    def __init__(self, folder: str, cap: int) -> None:
        self.folder, self.cap = folder, cap
        self.index = 0          # the piece being written
        self.total = 0
        # The fragmented MP4's opening - everything before the first moof.
        # A television that drops its connection during a pause and comes
        # back needs this before anything else will decode, and by then the
        # piece it was in has usually been thrown away.
        self.init = b""
        self._sniff: bytes | None = b""
        self.full = False       # the cap was reached; nothing more is kept
        self._w: object | None = None
        self._wrote = 0
        self._at: dict[int, int] = {}       # reader id -> piece it is on
        self.oldest = 0                     # the first piece still on disk
        self._lock = threading.Lock()
        os.makedirs(folder, exist_ok=True)

    def _path(self, i: int) -> str:
        return os.path.join(self.folder, f"piece{i:05d}")

    def write(self, data: bytes) -> bool:
        """Take *data*; False when the cap says to stop recording."""
        with self._lock:
            if self.full:
                return False
            if self._sniff is not None:
                self._sniff += data
                cut = self._sniff.find(b"moof")
                if cut >= 4:
                    self.init, self._sniff = self._sniff[:cut - 4], None
                elif len(self._sniff) > 4_000_000:
                    self._sniff = None      # not fragmented; give up looking
            while data:
                if self._w is None or self._wrote >= self.PIECE:
                    self._roll()
                room = self.PIECE - self._wrote
                self._w.write(data[:room])          # type: ignore[union-attr]
                self._w.flush()                     # type: ignore[union-attr]
                self._wrote += len(data[:room])
                self.total += len(data[:room])
                data = data[room:]
            self._prune()
            # How far ahead of the slowest reader we have got. That, and not
            # the length of the sending, is what a pause actually costs.
            behind = self.index - (min(self._at.values()) if self._at else 0)
            if behind * self.PIECE >= self.cap:
                log.info("cast bridge: the pause has reached %d GB of "
                         "recording - stopping there", self.cap // 10**9)
                self.full = True
                return False
        return True

    def _roll(self) -> None:
        if self._w is not None:
            self._w.close()                         # type: ignore[union-attr]
            self.index += 1
        self._w = open(self._path(self.index), "wb")
        self._wrote = 0

    def _prune(self) -> None:
        keep = min(self._at.values()) if self._at else self.index
        for i in range(self.oldest, max(0, keep - 1)):
            try:
                os.remove(self._path(i))
            except OSError:
                pass
            self.oldest = i + 1

    def first_kept(self) -> int:
        """The oldest piece still on disk. Where a television that dropped
        its connection during a pause has to pick the recording up again -
        starting at the beginning would be starting at a file that was
        thrown away twenty minutes ago, and it simply waited for ever."""
        with self._lock:
            return self.oldest

    def complete(self, i: int) -> bool:
        """Whether piece *i* has been written in full."""
        with self._lock:
            return i < self.index

    def reader(self) -> "_SpoolReader":
        return _SpoolReader(self)

    def close(self) -> None:
        with self._lock:
            if self._w is not None:
                try:
                    self._w.close()                 # type: ignore[union-attr]
                except OSError:
                    pass
                self._w = None


class _SpoolReader:
    """One television's way through the recording."""

    def __init__(self, spool: _Spool) -> None:
        self.spool = spool
        self.i = spool.first_kept()
        self._f: object | None = None
        # Coming in part way through means coming in without the opening the
        # decoder needs, and in the middle of a fragment. Hand over the
        # opening, then skip to where the next fragment starts.
        self._head = spool.init if self.i else b""
        self._align = self.i > 0
        spool._at[id(self)] = self.i

    def read(self, n: int) -> bytes:
        """The next bytes, or b"" when there are none yet."""
        if self._head:
            head, self._head = self._head, b""
            return head
        while True:
            if self._f is None:
                path = self.spool._path(self.i)
                if not os.path.exists(path):
                    return b""
                self._f = open(path, "rb")
            chunk = self._f.read(n)                 # type: ignore[union-attr]
            if chunk:
                if self._align:
                    cut = chunk.find(b"moof")
                    if cut < 4:
                        continue            # still mid-fragment; keep looking
                    self._align, chunk = False, chunk[cut - 4:]
                return chunk
            # Nothing more in this piece. Move on only once it is finished -
            # otherwise this is simply the write head, and more is coming.
            if not self.spool.complete(self.i):
                return b""
            self._f.close()                         # type: ignore[union-attr]
            self._f, self.i = None, self.i + 1
            self.spool._at[id(self)] = self.i

    def close(self) -> None:
        if self._f is not None:
            try:
                self._f.close()                     # type: ignore[union-attr]
            except OSError:
                pass
            self._f = None
        self.spool._at.pop(id(self), None)


# Where ffmpeg's transport-stream clock starts, in 90 kHz ticks. Measured
# with ffprobe on the first segment: 1.421333 s.
MPEGTS_START = 127920


def _timestamp_map(body: bytes) -> bytes:
    """Tie the subtitle's clock to the picture's, in the words HLS wants.

    A WebVTT segment carried in an HLS stream has to say how its own
    timeline relates to the transport stream's, and it says it with
    X-TIMESTAMP-MAP. ffmpeg writes none - its segments begin with a bare
    WEBVTT line - and a receiver handed cues it cannot place against the
    picture does not guess: it shows nothing at all, while reporting the
    track as present and switched on. Which is exactly what it did.

    MPEGTS_START is where the transport stream's clock actually begins.
    ffmpeg preloads it, measured at 1.421333 s - which is 90000 ticks a
    second, so 127920 - and the cues are written from zero. Saying so is
    what lines them up.

    Zeroing the mux preload instead was the other way to line them up, and
    it lined them up on nothing: the subtitles had been rendering, an
    offset ahead of the picture, and stopped rendering altogether. So the
    stream keeps the clock it had and this says where that clock starts,
    which is the mechanism HLS provides for exactly this.
    """
    head = body[:64].lstrip()
    if not head.startswith(b"WEBVTT") or b"X-TIMESTAMP-MAP" in body[:512]:
        return body                     # not ours to touch, or already said
    cut = body.index(b"WEBVTT") + len(b"WEBVTT")
    # Whatever line ending this segment uses, keep using it.
    eol = b"\r\n" if body[cut:cut + 2] == b"\r\n" else b"\n"
    return (body[:cut] + eol
            + b"X-TIMESTAMP-MAP=MPEGTS:" + str(MPEGTS_START).encode()
            + b",LOCAL:00:00:00.000"
            + body[cut:])


def _autoselect_subtitles(body: bytes, lang: str = "") -> bytes:
    """Ask the receiver to show the subtitle, not merely to have it.

    ffmpeg writes the rendition as DEFAULT=YES and stops there. A receiver
    reads DEFAULT as "use this one IF subtitles are on at all", and its own
    answer to whether they are on is usually no - so a subtitle chosen in
    the dialog arrived listed and switched off. AUTOSELECT is the word for
    "turn it on to match", and FORCED=NO says it is an ordinary subtitle
    rather than one for foreign dialogue only.

    Done on the way out rather than by arguing with ffmpeg's muxer, which
    has no option for either.
    """
    out = []
    for line in body.split(b"\n"):
        if line.startswith(b"#EXT-X-MEDIA:") and b"TYPE=SUBTITLES" in line:
            if b"AUTOSELECT=" not in line:
                line += b",AUTOSELECT=YES"
            if b"FORCED=" not in line:
                line += b",FORCED=NO"
            # And which language it is. ffmpeg names the rendition
            # "subtitle_0" and says nothing else about it; a Cast receiver
            # will list a text track with no LANGUAGE and then decline to
            # draw it, which is the state this arrived in - fetched,
            # switched on, and invisible.
            if b"LANGUAGE=" not in line and lang:
                line += b',LANGUAGE="' + lang.encode() + b'"'
        out.append(line)
    return b"\n".join(out)


class _Server(ThreadingHTTPServer):
    """The bridge's own HTTP server, with the shouting turned off.

    A Chromecast opens connections, probes them and drops them as a matter
    of course, and socketserver prints a full traceback for every one -
    pages of "Connection reset by peer" that look like the app falling over
    and say nothing about the cast.
    """

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError,
                            TimeoutError, ConnectionAbortedError)):
            return                      # the receiver hung up; not our news
        log.info("cast bridge: a request failed (%s)", exc)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # noqa: A003 - stdlib hook
        pass                                  # the app has its own log

    def _headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        # No length is known - the response ends when the stream does, which
        # for a live channel means when the cast is stopped.
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_HEAD(self) -> None:
        if self.path != self.server.bridge.path:
            self.send_error(404)
            return
        self._headers()

    _TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t",
              ".vtt": "text/vtt"}

    def _serve_hls(self) -> None:
        """Hand over one file of the HLS set.

        Everything the receiver asks for is named in a playlist we wrote, so
        only a plain name inside our own folder is ever answered - a path
        with a directory in it is somebody else asking for something else.

        The CORS header is not decoration here. A Cast receiver fetches the
        WebVTT rendition with a cross-origin request and drops it silently
        without one, which looks exactly like a stream that simply has no
        subtitles.
        """
        bridge = self.server.bridge
        name = self.path[len(bridge.prefix or ""):]
        # A plain name and nothing else. Everything the receiver asks for is
        # named in a playlist we wrote, so anything with a slash or a dot
        # path in it is somebody else asking for something else.
        if not name or "/" in name or name.startswith("."):
            self.send_error(404)
            return
        if not bridge.hls_ready(name):
            self.send_error(503)
            return
        path = os.path.join(bridge.hls_dir or "", name)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        if name == "master.m3u8":
            body = _autoselect_subtitles(body, bridge.sub_lang)
        elif name.endswith(".vtt"):
            body = _timestamp_map(body)
        # Once, and then quiet. Whether the receiver fetches the subtitles
        # at all is the one thing the sender cannot otherwise know - a
        # track can be present, switched on, and never read - so it is
        # worth a line. It is not worth one per segment for the length of a
        # film, which is what it was: forty lines a minute, saying the same
        # thing forty times.
        if name.endswith(".vtt") and not bridge.said_subs:
            bridge.said_subs = True
            log.info("cast bridge: the receiver is reading the subtitles "
                     "(%s, %d bytes)", name, len(body))
        self.send_response(200)
        self.send_header(
            "Content-Type",
            self._TYPES.get(os.path.splitext(name)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        bridge = self.server.bridge
        if bridge.hls and self.path.startswith(bridge.prefix or "\0"):
            self._serve_hls()
            return
        if self.path != bridge.path:
            self.send_error(404)
            return
        proc, spool = bridge.first_frames()
        if proc is None:
            self.send_error(503)
            return
        self._headers()
        began, sent = time.monotonic(), 0
        try:
            # Read from the spool, never from ffmpeg. A television takes its
            # stream at the speed it plays it, and reading ffmpeg directly
            # passed that speed all the way back up the chain: ffmpeg blocked
            # writing, stopped reading the panel, and the panel - which has
            # no patience for a connection nobody is reading - closed it.
            #   Stream ends prematurely at 29960221, should be 69423104
            # That was the whole failure. The spool absorbs the panel's burst
            # at full speed so the connection is always being drained, and
            # the receiver is served from disk at whatever pace suits it.
            reader = spool.reader()
            try:
                while True:
                    chunk = reader.read(65536)
                    if chunk:
                        self.wfile.write(chunk)
                        sent += len(chunk)
                        continue
                    if not bridge.filling(proc):
                        break
                    time.sleep(0.2)     # caught up with ffmpeg; wait for more
            finally:
                reader.close()
            log.info("cast bridge: ffmpeg stopped after %d s and %.1f MB "
                     "(exit %s) - the receiver has nothing more to play",
                     time.monotonic() - began, sent / 1e6,
                     proc.poll())
        except (BrokenPipeError, ConnectionResetError):
            log.info("cast bridge: the receiver closed the connection "
                     "after %d s", time.monotonic() - began)
        finally:
            bridge.reader_gone(proc)


class CastBridge:
    """Serves one transcoded stream on the LAN for as long as a cast runs."""

    def __init__(self) -> None:
        self.path: str | None = None
        self.prefix: str | None = None
        self.source: str | None = None
        # Delivered as HLS instead of one long fragmented MP4. Only for a
        # text subtitle, which travels beside the picture as a WebVTT
        # rendition rather than being drawn into it.
        self.hls = False
        self.hls_dir: str | None = None
        self.said_subs = False
        # When the last run was torn down. A panel goes on counting a
        # session for a moment after the socket closes, and this account
        # allows one - so a new ffmpeg started too soon is a second
        # connection as far as the provider is concerned, and it cuts one
        # of them.
        self.stopped_at = 0.0
        self.copy_video = True
        self.audio = 0
        self.subs: int | None = None
        self.sub_codec = ""
        self.sub_lang = ""
        self.start_at = 0.0
        self.quality = "original"
        # Set to ffmpeg's own words when it fails in a way that will fail the
        # same way every time. Read by spawn(), so a run that cannot work is
        # not attempted twice more while the TV waits.
        self.fatal: str = ""
        # How much room a pause may take. Set from Settings; the default is
        # about half an hour of a paused HD channel.
        self.cap = self.CAP
        # Which run of the bridge this is. A request that is still retrying
        # belongs to the run it began in - see first_frames().
        self.generation = 0
        self.exe: str | None = None          # overridable, for tests
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._procs: list[subprocess.Popen] = []
        # The thread draining each ffmpeg into its spool file.
        self._spools: dict[subprocess.Popen, threading.Thread] = {}
        # The stream now being served, and how many are reading it.
        self._current: tuple[subprocess.Popen, "_Spool", int] | None = None
        self._readers: dict[subprocess.Popen, int] = {}
        self._tmp: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        return ffmpeg_path() is not None

    def _safe_source(self, source: str) -> str:
        """A name for *source* that a filtergraph cannot misread.

        Escaping gets a colon through, but an apostrophe cannot be escaped
        reliably in every ffmpeg version - and "Ocean's Eleven 2026-07-28.ts"
        is an ordinary recording. A local file is therefore linked under a
        plain name in a temporary directory and ffmpeg is pointed at that,
        which sidesteps the whole quoting question. A URL is left alone: they
        are percent-encoded, so the only troublesome character they can carry
        is the colon in the port, which escaping handles.
        """
        if "://" in source:
            return source
        try:
            self._tmp = tempfile.mkdtemp(prefix="cast-", dir=cast_cache_dir())
            link = os.path.join(
                self._tmp, "source" + os.path.splitext(source)[1])
            os.symlink(os.path.abspath(source), link)
            return link
        except Exception as e:
            log.info("cast bridge: could not link the file (%s)", e)
            return source

    def start(self, source: str, codecs: list[str] | None = None,
              audio: int = 0, subs: int | None = None,
              sub_codec: str = "", start_at: float = 0.0,
              quality: str = "original", sub_lang: str = "") -> str:
        """Begin serving *source* re-muxed for the receiver; returns the URL.

        ffmpeg is not started here - it starts when the Chromecast actually
        asks for the stream, so a receiver that never connects costs nothing.
        """
        self.stop()
        self.fatal = ""          # a new run gets to fail on its own terms
        self.generation += 1
        source = self._safe_source(source)
        codecs = [c.lower() for c in (codecs or [])]
        # Copy the video unless it is something the receiver cannot decode.
        # Unknown codecs are assumed fine: re-encoding 1080p live on a laptop
        # is the expensive mistake, and being wrong the other way just means
        # the same refusal we already had.
        self.copy_video = not any(
            c not in SAFE_VIDEO and c not in SAFE_AUDIO and c in
            ("hevc", "h265", "vp9", "av1", "mpeg2", "mpeg2video")
            for c in codecs)
        self.source = source
        self.audio, self.subs, self.sub_codec = audio, subs, sub_codec
        # Three letters, the way a playlist wants them: "swe", not "Swedish".
        self.sub_lang = (sub_lang or "").strip()[:8]
        self.start_at = start_at
        self.quality = quality
        if QUALITY.get(quality, (0, 0)) != (0, 0):
            self.copy_video = False
        # A subtitle is no longer drawn into the picture - it travels beside
        # it as WebVTT - so choosing one is not a reason to re-encode a
        # single frame. This line was left over from when it was, and it
        # made every subtitled cast re-encode the whole film: hot, slow, and
        # slow enough that the provider hung up on a connection nobody was
        # draining fast enough.
        if subs is not None and sub_codec in BITMAP_SUBS:
            self.copy_video = False   # drawn in with overlay, so redrawn
        # A text subtitle goes beside the picture as a WebVTT rendition,
        # which means HLS: a set of files rather than one long response.
        self.hls = subs is not None and sub_codec not in BITMAP_SUBS
        self.prefix = f"/{secrets.token_urlsafe(12)}/"
        if self.hls:
            self.hls_dir = os.path.join(self._tmpdir(), "hls")
            os.makedirs(self.hls_dir, exist_ok=True)
            self.path = self.prefix + "master.m3u8"
        else:
            self.path = self.prefix + "stream.mp4"
        self._server = _Server(("0.0.0.0", 0), _Handler)
        self._server.bridge = self            # type: ignore[attr-defined]
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
            daemon=True)
        self._thread.start()
        port = self._server.server_address[1]
        # Anything still running belongs to the run just torn down. A
        # receiver request can land between stop() and here, and it starts
        # ffmpeg against a directory that has just been deleted:
        #   Could not write header (incorrect codec parameters ?): No such
        #   file or directory
        # - and worse, its entry then counts as "this run already has a
        # converter", so the real one never starts and the cast dies with
        # no playlist at all. Kill the straggler and forget it; it is also
        # holding a provider connection this account cannot spare.
        with self._lock:
            strays, self._procs = list(self._procs), []
        for old in strays:
            log.info("cast bridge: a converter from the last run was still "
                     "starting - stopping it")
            self.kill(old)
        url = f"http://{lan_address()}:{port}{self.path}"
        log.info("cast bridge: serving %s (video %s, audio track %d -> aac%s)",
                 url, "copied" if self.copy_video else "re-encoded",
                 self.audio,
                 (f", subtitle track {subs} as webvtt beside it" if self.hls
                  else f", subtitle track {subs} drawn in"
                  if subs is not None else "")
                 + ("" if quality == "original" else f", {quality}"))
        return url

    # How much has to come out of ffmpeg before a byte of it is handed to
    # the receiver. A run that dies before this never happened as far as the
    # TV is concerned, and can simply be tried again; one that dies after it
    # cannot, because a fragmented MP4 has no way to start over mid-response.
    # How far the recording may run ahead of the television - which is to
    # say, how long a pause may be. Not the length of the sending: what has
    # been watched is thrown away as it goes, so an evening's viewing costs
    # the same few seconds of disk as a minute of it.
    CAP = 4_500_000_000      # about half an hour of a paused HD channel
    OPENING = 64_000         # enough to know bytes are coming out at all
    SETTLE = 0.7
    # How long a provider goes on counting a session after we close it.
    SETTLE_AFTER_STOP = 2.0             # and long enough for a doomed run to fall over

    def _tmpdir(self) -> str:
        """The scratch directory this run of the bridge owns.

        Under the app's cache directory, never the system temp folder: on
        many Linux systems /tmp is tmpfs, which is RAM - and a paused
        broadcast can run to gigabytes. It is also never the recordings
        folder: nothing here is a recording anyone asked to keep, and it is
        all deleted the moment the cast ends.
        """
        if not self._tmp:
            self._tmp = tempfile.mkdtemp(prefix="cast-", dir=cast_cache_dir())
        return self._tmp

    # How long the receiver may be kept waiting for the first playlist.
    # ffmpeg has to open the provider, find the streams and write a segment
    # before there is anything to hand over, and over a slow link that is
    # seconds rather than milliseconds.
    HLS_WAIT = 25.0

    def hls_ready(self, name: str) -> bool:
        """Whether *name* can be served, starting ffmpeg if it has not been.

        Nothing runs until the receiver actually asks, exactly as for a
        single-response cast: a television that never connects costs the
        provider nothing.

        Only the first playlist is waited for. Everything after it is named
        in a playlist we wrote, so by the time it is asked for it exists -
        and waiting on a segment that ffmpeg has not reached yet would hold
        a connection open for the whole of a live stream.
        """
        folder = self.hls_dir
        if not folder:
            return False
        if not self._procs and not self.fatal and self.spawn() is None:
            return False
        if os.path.exists(os.path.join(folder, name)):
            return True
        if not name.endswith(".m3u8"):
            return False
        deadline = time.monotonic() + self.HLS_WAIT
        while time.monotonic() < deadline:
            if os.path.exists(os.path.join(folder, name)):
                log.info("cast bridge: the playlist is ready")
                return True
            if self.fatal or not any(p.poll() is None for p in self._procs):
                break               # ffmpeg gave up; nothing is coming
            time.sleep(0.2)
        log.info("cast bridge: no playlist after %.0f s - nothing to hand "
                 "the receiver", self.HLS_WAIT)
        return False

    def lead(self) -> str:
        """How far ahead of the television the converter is, in words.

        The one number that separates the two reasons a receiver
        rebuffers, and it has never been in the log:

          a small or shrinking lead - ffmpeg cannot keep up, and the TV is
          catching it. The cure is upstream (the provider, the encoder).
          a lead at the cap - the spool is full, so ffmpeg is BLOCKED
          writing, so it stops reading the provider, so the panel hangs up
          and the reconnect stalls the picture. The cure is the cap.

        Both look identical on screen: BUFFERING, PLAYING, BUFFERING.
        """
        if self.hls:
            folder = self.hls_dir or ""
            try:
                segs = [f for f in os.listdir(folder) if f.endswith(".ts")]
                mb = sum(os.path.getsize(os.path.join(folder, f))
                         for f in segs) / 1e6
            except OSError:
                return "writing a playlist"
            return f"{len(segs)} segments written, {mb:.0f} MB"
        cur = self._current
        if not cur:
            return "nothing running"
        _proc, spool, _n = cur
        ahead = spool.total - spool.read_to
        return (f"{ahead / 1e6:.0f} MB ahead"
                + (" - SPOOL FULL, ffmpeg is blocked" if spool.full else ""))

    def filling(self, proc: subprocess.Popen) -> bool:
        """Whether more is still on its way into the spool."""
        th = self._spools.get(proc)
        return bool(th and th.is_alive())

    def _spool_out(self, proc: subprocess.Popen, spool: "_Spool") -> None:
        """Drain ffmpeg into *path* as fast as it will come.

        This is the whole point of the spool: ffmpeg must never wait for the
        television. A panel closes an archive connection that nobody is
        reading, and reading ffmpeg at the receiver's pace made exactly that
        happen, a few seconds into every stretch.
        """
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                if not spool.write(chunk):
                    break               # the pause has gone on long enough
        except Exception as e:
            log.info("cast bridge: the spool stopped (%s)", e)
        finally:
            spool.close()

    def reader_gone(self, proc: subprocess.Popen) -> None:
        """A reader finished. Keep ffmpeg for whoever else is still reading.

        Killing it on the first disconnect is what turned a Chromecast's
        ordinary reconnect into a fresh ffmpeg, a fresh request to the panel
        and a stretch played from its beginning again - on an account with
        one connection, the previous one still counted, so the new one was
        cut short as well.
        """
        with self._lock:
            self._readers[proc] = self._readers.get(proc, 1) - 1
            if self._readers[proc] > 0:
                return
            self._readers.pop(proc, None)
        self.kill(proc)

    def first_frames(self) -> tuple[subprocess.Popen | None, "_Spool | None"]:
        """Start ffmpeg and hold the opening back until it is plainly going.

        These panels refuse a stream while they are still counting a
        connection that has just been dropped - and on the pause-and-resume
        path one is always dropped a moment earlier, because the receiver
        only lets go of the live stream when the archive replaces it. ffmpeg
        then reads a fraction of a second and stops:

            Error during demuxing: Input/output error
            ffmpeg stopped after 0 s and 0.9 MB (exit 0)

        Those nine hundred kilobytes used to go straight to the TV, which
        played them and reported IDLE/FINISHED - a black screen, from a cast
        that had simply been asked a few seconds too early. The count frees
        up within seconds, so hold the opening back until there is enough of
        it to be sure, and until then a failure costs nothing but a wait.
        """
        mine = self.generation
        # Already running for this stretch? Then this is the same stream
        # asked for twice - a receiver reconnecting, or checking - and it
        # reads the spool that is already filling. Starting a second ffmpeg
        # for it is what "it keeps restarting" was made of.
        with self._lock:
            live = self._current
            if (live and live[2] == mine and live[0].poll() is None):
                self._readers[live[0]] = self._readers.get(live[0], 0) + 1
                log.info("cast bridge: another reader joined the stream "
                         "already running")
                return live[0], live[1]
        for wait in (0, 6, 10):
            if wait:
                log.info("cast bridge: the stream stopped short - trying "
                         "again in %d s", wait)
                time.sleep(wait)
            # The bridge may have been started again while this request was
            # waiting - a track changed, a film moved to another point. That
            # is a different stream with a different receiver behind it, and
            # spawning into it put TWO ffmpeg on the same account: they took
            # a connection each, cut each other off, and neither arrived.
            if self.generation != mine:
                log.info("cast bridge: this request belongs to a stream that "
                         "has been replaced - letting it go")
                return None, b""
            proc = self.spawn()
            if proc is None:
                return None, None
            began = time.monotonic()
            spool = _Spool(os.path.join(self._tmpdir(),
                                        f"spool{self.generation}"),
                           self.cap)
            th = threading.Thread(target=self._spool_out, args=(proc, spool),
                                  daemon=True)
            with self._lock:
                self._spools[proc] = th
            th.start()
            while th.is_alive() and spool.total < self.OPENING:
                time.sleep(0.05)
            size = spool.total
            # Not how much came out, but whether the run is sound. One the
            # panel refuses dies with almost nothing and a non-zero code; one
            # that is still going, or that finished cleanly with a stretch
            # worth playing, is served either way - a stretch that simply
            # ended is what the continuation is for.
            if size >= self.OPENING:
                time.sleep(self.SETTLE)
                if proc.poll() in (None, 0) and self.generation == mine:
                    log.info("cast bridge: the stream is going (%.1f s to "
                             "the first %d kB)", time.monotonic() - began,
                             size // 1000)
                    with self._lock:
                        self._current = (proc, spool, mine)
                        self._readers[proc] = 1
                    return proc, spool
            self.kill(proc)
        log.info("cast bridge: the stream would not start")
        return None, None

    def spawn(self) -> subprocess.Popen | None:
        exe = self.exe or ffmpeg_path()
        # A stopped bridge has no source, and a retry that ignored that
        # resurrected ffmpeg after the cast had already moved on - two of them
        # then read the same channel and fought over the one connection.
        if not exe or not self.source or self._server is None:
            return None
        if self.fatal:
            log.info("cast bridge: not trying again - %s", self.fatal)
            return None
        if self.hls:
            args = hls_args(exe, self.source, self.copy_video,
                            self.hls_dir or "", self.audio, self.subs,
                            self.start_at, self.quality,
                            live=_endless(self.source))
        else:
            args = ffmpeg_args(exe, self.source, self.copy_video,
                               self.audio, self.subs, self.sub_codec,
                               self.start_at, self.quality)
        # Let the provider notice the last one has gone. Changing subtitle
        # three times in a row does three of these in a few seconds, and
        # the third found the panel still counting the second:
        #   Stream ends prematurely at 1500194040, should be 4785883508
        # - after which there is nothing to make segments from and the
        # picture freezes between BUFFERING and PLAYING for ever.
        wait = self.SETTLE_AFTER_STOP - (time.monotonic() - self.stopped_at)
        if 0 < wait <= self.SETTLE_AFTER_STOP:
            log.info("cast bridge: letting the provider release the last "
                     "connection (%.1f s)", wait)
            time.sleep(wait)
        log.info("cast bridge: starting ffmpeg")
        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            log.info("cast bridge: could not start ffmpeg (%s)", e)
            return None
        # ffmpeg says why it failed on stderr and nowhere else. Throwing that
        # away would leave exactly the kind of silent failure this whole
        # feature exists to end, so it goes into the log (loglevel is 'error',
        # so a working stream says nothing at all).
        threading.Thread(target=self._drain_errors, args=(proc, self),
                         daemon=True).start()
        with self._lock:
            self._procs.append(proc)
        return proc

    @staticmethod
    def _drain_errors(proc: subprocess.Popen, bridge: "CastBridge | None" = None
                      ) -> None:
        """ffmpeg's own words, with the repetition taken out.

        Picking a live transport stream up mid-broadcast means decoding
        before the first keyframe, and ffmpeg complains about every frame
        until one arrives. It does not complain in one voice: "non-existing
        PPS 0 referenced", "no frame!" and its own "Last message repeated"
        take turns, so no two consecutive lines are equal and counting equal
        ones catches none of it - hundreds of lines bury everything else.

        They are all the same event, and it is an expected one, so treat them
        as such: say it once, in ffmpeg's own words so the words are still
        searchable, then count the rest and report the total when the stream
        finally gets going.
        """
        last, repeats = "", 0
        noisy = 0

        def flush():
            if repeats:
                log.info("cast bridge: ffmpeg: (last line ×%d)", repeats + 1)

        def flush_noise():
            nonlocal noisy
            if noisy > 1:
                log.info("cast bridge: ffmpeg: (%d more while waiting for "
                         "the first keyframe)", noisy - 1)
            noisy = 0

        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if _mid_stream_noise(line):
                    flush()
                    last, repeats = "", 0
                    noisy += 1
                    if noisy == 1:
                        log.info("cast bridge: ffmpeg: %s (joined mid-stream "
                                 "- more of these until the first keyframe)",
                                 line)
                    continue
                flush_noise()
                # The FIRST one: "No such filter: 'subtitles'" is the reason,
                # and "Error opening output file" is only its consequence.
                if bridge is not None and not bridge.fatal \
                        and _fatal_error(line):
                    bridge.fatal = line
                if line == last:
                    repeats += 1
                    continue
                flush()
                last, repeats = line, 0
                log.info("cast bridge: ffmpeg: %s", line)
            flush()
            flush_noise()
        except Exception:
            pass

    def kill(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if proc in self._procs:
                self._procs.remove(proc)
            self._spools.pop(proc, None)
            self._readers.pop(proc, None)
            if self._current and self._current[0] is proc:
                self._current = None
        # Short waits on purpose: this runs on the way out of the app, where
        # every second spent here is a second the whole shutdown does not
        # have. ffmpeg has nothing to flush - it writes to a pipe nobody is
        # reading any more.
        for step, grace in ((proc.terminate, 1.0), (proc.kill, 0.5)):
            try:
                step()
                proc.wait(timeout=grace)
                return
            except Exception:
                continue

    def stop(self) -> None:
        with self._lock:
            procs, self._procs = list(self._procs), []
        for p in procs:
            self.kill(p)
        ran = self._server is not None
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            log.info("cast bridge: stopped")
        self._server = None
        self._thread = None
        # Only when something was actually torn down. Setting it
        # unconditionally made every cast wait two seconds for a connection
        # that had never been opened - start() tears down first, always, so
        # a fresh cast paid the price of a re-cast.
        if procs or ran:
            self.stopped_at = time.monotonic()
        self.path = self.source = self.prefix = None
        self.hls, self.hls_dir = False, None
        self.said_subs = False
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
