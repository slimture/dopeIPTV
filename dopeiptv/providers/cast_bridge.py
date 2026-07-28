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


# How the picture is adapted for a receiver that cannot keep up with it.
# Deinterlacing is not part of the choice: no Chromecast deinterlaces at all,
# so it is applied whenever the video is re-encoded anyway - with deint=1,
# which touches only frames actually marked as interlaced.
# "older" is the only adaptation offered, under a name that describes the
# problem rather than the mechanism.
#
# It caps the number of LINES and nothing else. The frame rate used to be
# capped at 30 as well, on the reasoning that an older receiver would be
# short of both - but measured against a first-generation dongle it is only
# the lines: HD channels play perfectly there and Swedish HD is 720p50, so
# fifty frames a second is plainly within reach. Only FHD stutters. Halving
# the frame rate of a channel that never needed it made the picture visibly
# worse to fix a problem it did not have.
QUALITY = {"original": (0, 0), "older": (720, 0)}


def normalise_quality(value: str | None) -> str:
    """A stored setting, in today's terms.

    The question used to be a three-way choice and was written down as
    "720p" or "720p30". Those devices are still set that way, and "720p30"
    is no longer a thing that can be asked for - so read it as the answer it
    was, which is yes.
    """
    return "original" if not value or value == "original" else "older"

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
_can_burn: bool | None = None
_ffmpeg: str | None | bool = False       # False = not looked for yet


def _has_subtitles_filter(exe: str) -> bool:
    """Whether this particular ffmpeg was built with libass.

    Asked about the one filter rather than read out of the whole table: the
    table's columns are a display format, and a build that lays them out
    differently would be misread as having no libass while it does.
    """
    try:
        r = subprocess.run([exe, "-hide_banner", "-h", "filter=subtitles"],
                           capture_output=True, text=True, timeout=15)
    except Exception as e:
        log.info("cast bridge: could not ask %s about its filters (%s)",
                 exe, e)
        return False
    said = (r.stdout or "") + (r.stderr or "")
    return "unknown filter" not in said.lower() and "subtitles" in said


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


def can_burn_subtitles(exe: str | None = None) -> bool:
    """Whether this ffmpeg can burn a text subtitle into the picture.

    The subtitles filter is built on libass, and plenty of ffmpeg builds ship
    without it - "No such filter: 'subtitles'", said once per attempt.

    There is no way round it on the receiver's side. A Chromecast renders one
    kind of subtitle only: a WebVTT file handed to it alongside the media. For
    a live channel there is nothing to hand over. For a film there is, in
    principle - but making it means demuxing the whole file first, which over
    a provider link is minutes of waiting before anything appears on the TV,
    and the receiver additionally requires CORS headers on the media itself,
    which the provider does not send. So burning it into the picture is the
    only route either way, and this decides whether the choice is offered at
    all rather than being discovered after the picture has already gone.

    Bitmap subtitles are a different matter - they are drawn with overlay,
    which every build has.
    """
    global _can_burn
    if exe is not None:                  # a named build, asked about directly
        return _has_subtitles_filter(exe)
    if _can_burn is None:
        chosen = ffmpeg_path()
        _can_burn = bool(chosen) and _has_subtitles_filter(chosen)
    return _can_burn


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

    Not simply the first one on PATH. Whether a build has libass decides
    whether a subtitle can be sent to the TV at all, and machines routinely
    carry more than one ffmpeg - a slim one on PATH and a full one from
    Homebrew, or a bundled one inside the app. So the ones that are here are
    asked, and a build that can burn subtitles wins over one that cannot.
    Asked once: it cannot change while the app is running.
    """
    global _ffmpeg, _can_burn
    if _ffmpeg is not False:
        return _ffmpeg                   # type: ignore[return-value]
    found = _ffmpeg_candidates()
    _ffmpeg, _can_burn = (found[0] if found else None), False
    for cand in found:
        if _has_subtitles_filter(cand):
            _ffmpeg, _can_burn = cand, True
            break
    if _ffmpeg is None:
        log.info("cast bridge: no ffmpeg found - streams the receiver "
                 "refuses cannot be converted")
    else:
        log.info("cast bridge: using %s (subtitles %s)", _ffmpeg,
                 "can be burned in" if _can_burn else
                 "cannot be sent - this build has no libass")
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
    if _local_url(source):
        # Our own source spool. Nothing to reconnect to and nothing to
        # pretend to be - and a reconnect here would be actively harmful,
        # because the spool is served from the beginning and ffmpeg would
        # restart the film rather than carry on.
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


def _local_url(source: str) -> bool:
    """Whether *source* is served by this machine - our own source spool."""
    return source.startswith(("http://127.0.0.1:", "http://localhost:"))


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
            # The subtitles filter reads the file from its own copy, and that
            # copy knows nothing of a seek made before -i: the video arrives
            # with timestamps starting at zero, the filter looks for a line
            # to show at zero seconds, and NOTHING is drawn at all. Not a
            # line out of step - none.
            #
            # So hand the filter the timeline it expects and take it back
            # afterwards: shift the frames up to where they really are in the
            # film, render, shift them down again. The seek stays the cheap
            # kind and the stream still starts at zero, which is what the
            # receiver needs.
            #
            # "filename=" spelled out, not left positional. Newer ffmpeg
            # refuses to take the first argument as a bare value once it has
            # been escaped, and says so about the whole rest of the chain:
            #   No option name near 'http\://lol.bz\:2095/....mkv:si=4'
            if start > 0:
                chain.append(f"setpts=PTS+{start:.3f}/TB")
            chain.append(
                f"subtitles=filename={_filter_escape(source)}:si={subs}")
            if start > 0:
                chain.append(f"setpts=PTS-{start:.3f}/TB")
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
        "-analyzeduration", "1000000", "-probesize", "1000000",
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


class _SourceSpool:
    """One connection to the provider, drained to disk, so that ffmpeg may
    open the source as many times as it likes.

    The subtitles filter does not take a subtitle stream. It takes a
    FILENAME, and opens it itself - so burning a text subtitle into the
    picture cost three opens of the source where an ordinary cast costs one.
    Measured, on a counting server:

        without a subtitle . . . 1 open
        with the filter  . . . . 3 opens

    An Xtream account allows one. The panel hung up on the main connection
    the moment the second arrived, and the film stopped a minute in:

        Stream ends prematurely at 49211312, should be 4785883508

    This is the same illness the pause had, and the same cure: stop asking
    the provider for it. One connection is drained here at full speed into a
    file, and ffmpeg is pointed at a local address instead - all three opens
    land on this machine and the provider sees exactly one.

    Two more things were measured before building it. The subtitles filter
    is happy with a file that is not finished - it renders identically
    against half of one - but ffmpeg reading a file that is still growing
    STOPS at the end of it, and got 1.8 seconds of a 20-second clip. So the
    spool is served over HTTP by something that waits at the write head
    rather than answering EOF, which is what the reader below is for.
    """

    CHUNK = 262_144

    def __init__(self, folder: str, url: str, cap: int) -> None:
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, "source.bin")
        self.url, self.cap = url, cap
        self.done = 0               # bytes on disk, and therefore readable
        # How long the whole thing is, when the provider says so. Passed on
        # to ffmpeg, because without it a clean end of file is read as a
        # truncation and the demuxer stops with an I/O error:
        #   Stream ends prematurely at 7968, should be 18446744073709551615
        # (that second number is "unknown"). A live stream has no length and
        # none is sent, which is the same as the provider's own answer.
        self.size = 0
        self.finished = False
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._fetch, daemon=True)

    def start(self) -> "_SourceSpool":
        self._thread.start()
        return self

    def _fetch(self) -> None:
        """Take the stream as fast as it will come. Never at the pace of
        anything downstream - a panel closes a connection nobody is
        reading, and that is the failure this whole class exists to avoid."""
        import urllib.request
        began = time.monotonic()
        try:
            req = urllib.request.Request(
                self.url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=30) as r, \
                    open(self.path, "wb") as f:
                try:
                    self.size = int(r.headers.get("Content-Length") or 0)
                except ValueError:
                    self.size = 0
                while not self._stop.is_set():
                    chunk = r.read(self.CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    f.flush()
                    self.done += len(chunk)
                    if self.done >= self.cap:
                        log.info("cast bridge: the source reached %d GB - "
                                 "not taking any more of it",
                                 self.cap // 10**9)
                        break
        except Exception as e:
            # Not fatal by itself: what has already arrived still plays, and
            # the reader below will simply run out where the download did.
            self.error = str(e)
            log.info("cast bridge: the source stopped coming (%s)", e)
        finally:
            self.finished = True
            log.info("cast bridge: source spool holds %.1f MB after %d s",
                     self.done / 1e6, time.monotonic() - began)

    def stop(self) -> None:
        self._stop.set()

    def reader(self, at: int = 0) -> "_SourceReader":
        return _SourceReader(self, at)


class _SourceReader:
    """One of ffmpeg's opens of the source, read from the spool.

    Always from the beginning, always forwards, and it waits at the write
    head instead of reporting the end - which is the difference between a
    film that plays and 1.8 seconds of one.
    """

    def __init__(self, spool: _SourceSpool, at: int = 0) -> None:
        self.spool = spool
        self.pos = at
        self._f = None

    def read(self, n: int) -> bytes:
        while True:
            if self.spool.size and self.pos >= self.spool.size:
                return b""              # the whole thing has been handed on
            ready = self.spool.done - self.pos
            if ready > 0:
                if self._f is None:
                    self._f = open(self.spool.path, "rb")
                self._f.seek(self.pos)
                data = self._f.read(min(n, ready))
                self.pos += len(data)
                return data
            if self.spool.finished:
                return b""              # the download really has ended
            time.sleep(0.05)

    def close(self) -> None:
        if self._f is not None:
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None


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

    def _serve_source(self) -> None:
        """Hand ffmpeg the source out of the spool.

        Deliberately not seekable. A Range is read and answered 200 from the
        beginning, and no Accept-Ranges is offered - which is how ffmpeg
        decides a stream cannot be jumped around in.

        That is the whole difference between this working and not. Offered a
        seekable Matroska, ffmpeg's first move is to fetch the index, which
        lives at the END of the file: it asked for byte 4785867699 of
        4785883508 before it had read a single frame. A spool is filled from
        the front, so the answer was to wait for a 4.7 GB download, and the
        television sat black through all of it. Told the stream cannot be
        jumped around in, the demuxer reads it straight through instead -
        exactly what it does with a live channel, and exactly what all three
        of the filtergraph's opens want anyway.

        The length is still sent, because without it a clean end of file
        reads as a truncation and the demuxer stops with an I/O error.
        """
        src = self.server.bridge.src
        if src is None:
            self.send_error(404)
            return
        size = src.size
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        if size:
            self.send_header("Content-Length", str(size))
        self.send_header("Connection", "close")
        self.end_headers()
        reader = src.reader()
        try:
            while True:
                chunk = reader.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass                    # ffmpeg closed one of its opens; normal
        finally:
            reader.close()

    def do_GET(self) -> None:
        bridge = self.server.bridge
        if bridge.src_path and self.path == bridge.src_path:
            self._serve_source()
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
        self.source: str | None = None
        # The provider's stream, taken once and kept here, for the cast that
        # needs to open it more than once. None for every other cast.
        self.src: "_SourceSpool | None" = None
        self.src_path: str | None = None
        self.copy_video = True
        self.audio = 0
        self.subs: int | None = None
        self.sub_codec = ""
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
              quality: str = "original") -> str:
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
        self.start_at = start_at
        self.quality = quality
        if QUALITY.get(quality, (0, 0)) != (0, 0):
            self.copy_video = False
        if subs is not None:
            self.copy_video = False   # burning subtitles in redraws every frame
        self.path = f"/{secrets.token_urlsafe(12)}/stream.mp4"
        self._server = _Server(("0.0.0.0", 0), _Handler)
        self._server.bridge = self            # type: ignore[attr-defined]
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
            daemon=True)
        self._thread.start()
        port = self._server.server_address[1]
        # Burning a text subtitle in means ffmpeg opens the source three
        # times over, and an account that allows one connection loses the
        # stream to the second. Take it once, here, and let all three opens
        # land on this machine. Only for a remote source: a file on disk can
        # already be opened as often as anyone likes.
        if (subs is not None and sub_codec not in BITMAP_SUBS
                and "://" in source):
            self.src_path = f"/{secrets.token_urlsafe(12)}/source"
            self.src = _SourceSpool(self._tmpdir(), source, self.cap).start()
            self.source = source = f"http://127.0.0.1:{port}{self.src_path}"
            log.info("cast bridge: taking the source once into a spool - "
                     "the subtitles filter opens it again by itself")
        url = f"http://{lan_address()}:{port}{self.path}"
        log.info("cast bridge: serving %s (video %s, audio track %d -> aac%s)",
                 url, "copied" if self.copy_video else "re-encoded",
                 self.audio,
                 (f", subtitle track {subs} burned in"
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
    SETTLE = 0.7             # and long enough for a doomed run to fall over

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
        args = ffmpeg_args(exe, self.source, self.copy_video,
                           self.audio, self.subs, self.sub_codec,
                           self.start_at, self.quality)
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
        if self.src is not None:
            # Before the server goes: the download holds a provider
            # connection, and leaving it running would keep the account
            # busy for a cast nobody is watching any more.
            self.src.stop()
            self.src = None
        self.src_path = None
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            log.info("cast bridge: stopped")
        self._server = None
        self._thread = None
        self.path = self.source = None
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
