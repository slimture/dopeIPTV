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
import secrets
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..core.log import log

# What a Cast receiver decodes. Anything outside these has to be re-encoded;
# anything inside is copied through untouched.
SAFE_VIDEO = {"h264", "avc1", "vp8"}
SAFE_AUDIO = {"aac", "aac-latm", "mp3", "vorbis", "opus"}

# A player User-Agent: panels routinely refuse anything that looks automated.
_UA = "VLC/3.0.20 LibVLC/3.0.20"


def ffmpeg_path() -> str | None:
    """Where ffmpeg is, or None when it is not installed."""
    return shutil.which("ffmpeg")


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
    exe = exe or (shutil.which("ffprobe") or "")
    out: dict[str, list[dict]] = {"audio": [], "subtitle": []}
    if not exe:
        return out
    try:
        raw = subprocess.run(
            [exe, "-v", "error", *(["-user_agent", _UA]
                                   if "://" in source else []),
             "-print_format", "json", "-show_streams", source],
            capture_output=True, timeout=25).stdout
        streams = json.loads(raw or b"{}").get("streams", [])
    except Exception as e:
        log.info("cast bridge: could not list the tracks (%s)", e)
        return out
    for s in streams:
        kind = s.get("codec_type")
        if kind not in out:
            continue
        tags = s.get("tags") or {}
        out[kind].append({
            "index": len(out[kind]),          # what -map 0:a:N / 0:s:N counts
            "codec": s.get("codec_name") or "?",
            "lang": (tags.get("language") or "").strip(),
            "title": (tags.get("title") or "").strip(),
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
    """
    if "://" not in source:
        return []
    return ["-user_agent", _UA,
            "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1", "-reconnect_delay_max", "5"]


def _filter_escape(source: str) -> str:
    """Escape a source path for use inside a filtergraph argument."""
    for ch in ("\\", "'", ":", ",", "[", "]"):
        source = source.replace(ch, "\\" + ch)
    return source


def ffmpeg_args(exe: str, source: str, copy_video: bool,
                audio: int = 0, subs: int | None = None,
                sub_codec: str = "") -> list[str]:
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
    burn: list[str] = []
    if subs is not None:
        copy_video = False                    # a picture that changes must be
        if sub_codec in BITMAP_SUBS:          # re-encoded, whatever it held
            burn = ["-filter_complex",
                    f"[0:v:0][0:s:{subs}]overlay[v]", "-map", "[v]"]
        else:
            burn = ["-vf",
                    f"subtitles='{_filter_escape(source)}':si={subs}",
                    "-map", "0:v:0"]
    else:
        burn = ["-map", "0:v:0"]
    return [
        exe, "-hide_banner", "-loglevel", "error",
        *_input_options(source),
        "-fflags", "+genpts", "-i", source,
        *burn, "-map", f"0:a:{audio}",
        "-c:v", "copy" if copy_video else "libx264",
        *([] if copy_video else
          ["-preset", "veryfast", "-crf", "23",
           "-maxrate", "6M", "-bufsize", "12M"]),
        "-c:a", "aac", "-ac", "2", "-b:a", "192k",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "pipe:1",
    ]


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

    def do_GET(self) -> None:
        bridge = self.server.bridge
        if self.path != bridge.path:
            self.send_error(404)
            return
        proc = bridge.spawn()
        if proc is None:
            self.send_error(503)
            return
        self._headers()
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            log.info("cast bridge: the receiver closed the connection")
        finally:
            bridge.kill(proc)


class CastBridge:
    """Serves one transcoded stream on the LAN for as long as a cast runs."""

    def __init__(self) -> None:
        self.path: str | None = None
        self.source: str | None = None
        self.copy_video = True
        self.audio = 0
        self.subs: int | None = None
        self.sub_codec = ""
        self.exe: str | None = None          # overridable, for tests
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        return ffmpeg_path() is not None

    def start(self, source: str, codecs: list[str] | None = None,
              audio: int = 0, subs: int | None = None,
              sub_codec: str = "") -> str:
        """Begin serving *source* re-muxed for the receiver; returns the URL.

        ffmpeg is not started here - it starts when the Chromecast actually
        asks for the stream, so a receiver that never connects costs nothing.
        """
        self.stop()
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
        if subs is not None:
            self.copy_video = False   # burning subtitles in redraws every frame
        self.path = f"/{secrets.token_urlsafe(12)}/stream.mp4"
        self._server = ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        self._server.bridge = self            # type: ignore[attr-defined]
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
            daemon=True)
        self._thread.start()
        url = (f"http://{lan_address()}:{self._server.server_address[1]}"
               f"{self.path}")
        log.info("cast bridge: serving %s (video %s, audio track %d -> aac%s)",
                 url, "copied" if self.copy_video else "re-encoded",
                 self.audio,
                 f", subtitle track {subs} burned in" if subs is not None
                 else "")
        return url

    def spawn(self) -> subprocess.Popen | None:
        exe = self.exe or ffmpeg_path()
        if not exe or not self.source:
            return None
        args = ffmpeg_args(exe, self.source, self.copy_video,
                           self.audio, self.subs, self.sub_codec)
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
        threading.Thread(target=self._drain_errors, args=(proc,),
                         daemon=True).start()
        with self._lock:
            self._procs.append(proc)
        return proc

    @staticmethod
    def _drain_errors(proc: subprocess.Popen) -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    log.info("cast bridge: ffmpeg: %s", line)
        except Exception:
            pass

    def kill(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if proc in self._procs:
                self._procs.remove(proc)
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
