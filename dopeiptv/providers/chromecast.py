"""Chromecast discovery and casting (optional, via pychromecast)."""

from __future__ import annotations

import threading
import time

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QVBoxLayout,
)

from ..core.log import log
from ..i18n import tr
from ..core.workers import run_async
from .cast_bridge import SAFE_AUDIO, SAFE_VIDEO, CastBridge, probe_tracks

# pychromecast drags in zeroconf + ifaddr (~130 ms of the app's startup),
# and this module sits on the main window's import chain - so the import is
# deferred until casting is actually used (the Cast menu action).
_pychromecast = None
_pc_checked = False


def _pc():
    """Import pychromecast on first use; None when it isn't installed."""
    global _pychromecast, _pc_checked
    if not _pc_checked:
        _pc_checked = True
        try:
            import pychromecast
            _pychromecast = pychromecast
        except Exception:
            _pychromecast = None
    return _pychromecast


def cast_content_type(url: str | None) -> str:
    """Best-effort MIME type for the Chromecast receiver."""
    u = (url or "").lower().split("?")[0]
    if u.endswith(".m3u8"):
        return "application/x-mpegURL"
    if u.endswith(".ts"):
        return "video/mp2t"
    if u.endswith(".mkv"):
        return "video/x-matroska"
    if u.endswith(".webm"):
        return "video/webm"
    return "video/mp4"


# The receiver decodes none of these, whatever they are labelled: raw MPEG
# transport streams and Matroska are simply not on the Cast platform's list.
# Handing one over produces a silent IDLE/ERROR and nothing else, so say so
# instead of casting into the void.
_UNPLAYABLE = {"video/mp2t", "video/x-matroska", "video/mpeg",
               "video/x-msvideo", "video/x-flv"}


def _log_playlist_head(response) -> None:
    """Write the first few lines of the playlist we are about to hand over.

    A Chromecast that refuses a stream says only IDLE/ERROR - never why - and
    channels that play perfectly well in the app do get refused. This is the
    only place we ever see what the receiver is actually being pointed at, and
    it costs nothing: the connection is already open for the redirect check
    and closes right after.

    It answers, in one line, the questions that otherwise take a whole evening:
    is this really a playlist or a raw TS stream mislabelled as one, are the
    segments absolute URLs or paths, and does the manifest name codecs the
    receiver cannot decode.
    """
    try:
        head = next(response.iter_content(2048), b"") or b""
        text = head.decode("utf-8", "replace")
        if not text.lstrip().startswith("#EXTM3U"):
            log.info("cast: not a playlist (starts %r)", text[:24])
            return
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:8]
        log.info("cast: playlist head %s", " | ".join(lines))
    except Exception as e:
        log.debug("cast: playlist peek failed (%s)", e)


def _resolve_redirects(url: str) -> tuple[str, str | None]:
    """Follow the provider's redirects here, so the receiver is handed the
    address the stream actually lives at.

    Xtream panels redirect a channel URL to a CDN host, and the HLS playlist
    they serve there lists its segments as absolute PATHS ("/hls/.../x.ts")
    with no host. Those resolve against the playlist's own base URL - so a
    player that fetched the playlist through a redirect but keeps the original
    base looks for the segments on the wrong host and finds nothing. That is
    exactly what the Chromecast did: it loaded the manifest fine and then sat
    at IDLE/ERROR without ever showing a frame.

    Returns (url, content_type). The server's own Content-Type is kept
    because the resolved address usually has no file extension at all
    ("/live/play/<token>/26592"), and guessing from the extension then landed
    on the mp4 default - so the receiver was told an HLS playlist was an MP4,
    fetched it, found #EXTM3U and refused.

    Best effort: on any failure the original URL is used unchanged, which is
    no worse than before.
    """
    try:
        from ..core._lazy_requests import requests
        # Ask for the redirect only - never open the stream itself. Fetching
        # it (allow_redirects=True) made the panel reset the connection: it
        # costs one of the account's simultaneous connections, and this runs
        # while the receiver is about to take the very same slot. A player
        # User-Agent matters too - panels routinely refuse python-requests.
        headers = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}
        final, ctype = url, ""
        for _ in range(4):          # follow a short chain, never a loop
            r = requests.get(final, headers=headers, timeout=(3.05, 8),
                             allow_redirects=False, stream=True)
            loc = r.headers.get("Location")
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
            if not loc:
                _log_playlist_head(r)
                r.close()
                break
            r.close()
            final = requests.compat.urljoin(final, loc)
        if final != url:
            log.info("cast: resolved redirect -> %s", final)
        # text/html is the panel's error page, never a media type - ignore it.
        if ctype.startswith("text/"):
            ctype = ""
        return final, (ctype or None)
    except Exception as e:
        # Info, not debug: when this fails the receiver is handed a URL we
        # already know it usually cannot play, and the log then has to say so -
        # otherwise a failed cast looks identical to a dead channel.
        log.info("cast: redirect resolve failed (%s); using original URL", e)
        return url, None


# MPEG-TS stream_type -> codec, for the ones that decide whether a Chromecast
# can play a channel at all. A transport stream carries this in its PMT, which
# is why the answer is in the stream itself and nowhere else: an HLS media
# playlist lists bare segment paths and names no codecs.
_TS_TYPES = {
    0x01: "mpeg1", 0x02: "mpeg2", 0x03: "mp2", 0x04: "mp3",
    0x0F: "aac", 0x11: "aac-latm", 0x1B: "h264", 0x24: "hevc",
    0x81: "ac3", 0x87: "eac3", 0x06: "private",
}
# What every Cast receiver decodes. Everything else is a refusal on the older
# devices - HEVC has no decoder at all, and AC-3/E-AC-3 only pass through on
# Ultra and Google TV.
_CAST_CODECS = SAFE_VIDEO | SAFE_AUDIO


def _ts_codecs(data: bytes) -> list[str]:
    """Pull the codec list out of raw MPEG-TS bytes via PAT and PMT."""
    found: list[str] = []
    start = data.find(b"\x47")
    if start < 0:
        return found
    pmt_pid = None
    for i in range(start, len(data) - 187, 188):
        pkt = data[i:i + 188]
        if pkt[0] != 0x47 or not pkt[1] & 0x40:      # sync / payload start
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        adaptation = (pkt[3] >> 4) & 0x3
        if not adaptation & 0x1:                     # no payload
            continue
        off = 4 + (1 + pkt[4] if adaptation & 0x2 else 0)
        body = pkt[off:]
        if not body:
            continue
        body = body[1 + body[0]:]                    # skip pointer_field
        if len(body) < 13:
            continue
        section = 3 + (((body[1] & 0x0F) << 8) | body[2]) - 4   # minus CRC
        if pid == 0 and pmt_pid is None and body[0] == 0x00:
            for j in range(8, min(section, len(body)) - 3, 4):
                if (body[j] << 8) | body[j + 1]:     # program 0 is the NIT
                    pmt_pid = ((body[j + 2] & 0x1F) << 8) | body[j + 3]
                    break
        elif pid == pmt_pid and body[0] == 0x02:
            pos = 12 + (((body[10] & 0x0F) << 8) | body[11])
            while pos + 4 < min(section, len(body)):
                found.append(_TS_TYPES.get(body[pos], f"0x{body[pos]:02x}"))
                pos += 5 + (((body[pos + 3] & 0x0F) << 8) | body[pos + 4])
            break
    return found


def _probe_codecs(url: str) -> list[str]:
    """Read enough of the stream to say what is inside it.

    When a Chromecast refuses a channel it says IDLE/ERROR and nothing else -
    never which part it could not decode - and the playlist cannot answer it
    either. So fetch the first segment and read the transport stream's own
    program map. Two short requests, only ever on a cast that has already
    failed.
    """
    try:
        from ..core._lazy_requests import requests
        headers = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}
        r = requests.get(url, headers=headers, timeout=(3.05, 8), stream=True)
        head = next(r.iter_content(8192), b"") or b""
        base = r.url
        r.close()
        if head.lstrip().startswith(b"#EXTM3U"):
            seg = next((ln for ln in head.decode(
                "utf-8", "replace").splitlines()
                if ln.strip() and not ln.startswith("#")), None)
            if not seg:
                return []
            seg = requests.compat.urljoin(base, seg.strip())
            r = requests.get(seg, headers=headers, timeout=(3.05, 8),
                             stream=True)
            head = next(r.iter_content(65536), b"") or b""
            r.close()
        return _ts_codecs(head)
    except Exception as e:
        # Info, not debug: this is the step that decides whether the video can
        # be copied through, and its failure has to be visible.
        log.info("cast: could not read the stream's codecs (%s)", e)
        return []


class _CastWatch:
    """Writes down what the receiver and the sender socket do, for the whole
    life of a cast.

    Casting is one-way from here: play_media returns and everything that
    matters afterwards happens on the TV. When a cast dies minutes later there
    is otherwise no way to tell a receiver-side error from our own socket
    quietly going away - so both are logged as they happen.
    """

    def __init__(self, name: str, manager=None) -> None:
        self.name = name
        self.manager = manager
        self._last = ""

    def new_media_status(self, status) -> None:
        # Where the TV has got to. It is the only place that number exists -
        # the sender is not playing anything - and it is what a resume point
        # is made of.
        if self.manager is not None:
            try:
                pos = float(getattr(status, "current_time", 0) or 0)
                if pos > 0:
                    self.manager.last_position = pos
            except (TypeError, ValueError):
                pass
        cur = (f"{getattr(status, 'player_state', '?')}/"
               f"{getattr(status, 'idle_reason', None)}")
        if cur != self._last:
            self._last = cur
            log.info("cast %s: receiver %s", self.name, cur)

    def load_media_failed(self, queue_item_id, error_code) -> None:
        log.info("cast %s: receiver refused the stream (error %s)",
                 self.name, error_code)

    def new_connection_status(self, status) -> None:
        log.info("cast %s: sender connection %s",
                 self.name, getattr(status, "status", status))


class ChromecastManager:
    """Discovers Chromecast devices on the LAN and casts streams."""

    def __init__(self) -> None:
        self.devices: list = []
        self.active = None
        self._browser = None
        self.bridge = CastBridge()
        # Codecs a given device has been seen to refuse, so the same twenty
        # seconds of refusals are not spent on every cast. Learned from the
        # device itself and never assumed: a receiver that plays E-AC-3 keeps
        # getting the stream straight from the provider, untouched, and never
        # reaches the converter at all. Deliberately per session - a new
        # device, or new firmware, gets to answer for itself again.
        self._refused: dict[str, set[str]] = {}
        # And the same thing for a channel whose codecs we never identified:
        # remembered per channel, never per device, so a device that plays
        # most channels natively goes on doing exactly that.
        self._needs_bridge: set[tuple[str, str]] = set()
        # Where the current cast has got to, and how long the title is. The
        # receiver reports its own position from zero, so a cast that started
        # part way in has that offset added back before anything is stored.
        self.last_position = 0.0
        self.position_offset = 0.0
        self.duration = 0.0
        # Discovery runs in the worker pool and so can a cast - and a cast may
        # have to discover first (see cast()). Two of those at once would tear
        # down each other's devices mid-flight.
        self._lock = threading.RLock()

    @staticmethod
    def available() -> bool:
        return _pc() is not None

    def _tear_down(self) -> None:
        """Drop the previous round of devices, then the browser behind them.

        The order is the whole point. Every Chromecast object keeps the
        browser's zeroconf instance and its socket thread reaches for it
        whenever it reconnects - and stop_discovery() CLOSES that instance,
        even one it did not create. Stopping the browser while the devices
        were still alive therefore blew up inside pychromecast's own thread:

            AssertionError: Zeroconf instance loop must be running,
                            was it already stopped?

        Devices first, browser second, and nothing is left holding a closed
        zeroconf.
        """
        for cc in self.devices:
            try:
                cc.disconnect(timeout=2)
            except Exception:
                pass
        self.devices = []
        self.active = None
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
            self._browser = None

    def scan(self) -> list[str]:
        with self._lock:
            was_active = getattr(self.active, "name", None)
            self._tear_down()
            devices, browser = _pc().get_chromecasts(timeout=6)
            self._browser = browser
            self.devices = devices
            # A running cast survives all this: the receiver plays the stream
            # by itself, so dropping the sender socket does not stop the TV.
            # Re-point 'active' at the freshly discovered object with the same
            # name, or the stop button would have nothing left to talk to.
            if was_active:
                self.active = next(
                    (c for c in devices if c.name == was_active), None)
            return sorted(cc.name for cc in devices)

    def _device(self, name: str):
        return next((c for c in self.devices if c.name == name), None)

    def position(self) -> float:
        """How far into the title the TV has got, in seconds."""
        return self.position_offset + self.last_position

    def cast(self, device_name: str, url: str, title: str,
             known_codecs: list[str] | None = None,
             audio: dict | None = None, subs: dict | None = None,
             start: float = 0.0, duration: float = 0.0,
             settle: bool = False, source: str | None = None) -> str:
        with self._lock:
            cc = self._device(device_name)
            if cc is None:
                # The dialog offers the devices you cast to last time before
                # discovery has finished, so the first press can name a device
                # this run has not met yet - and casting then always took two
                # presses, the first one only to be told to rescan. Discover
                # now instead. The lock means a press during the opening scan
                # simply waits for it rather than starting a second one.
                log.info("cast: %s not discovered yet - scanning first",
                         device_name)
                self.scan()
                cc = self._device(device_name)
            if cc is None:
                raise RuntimeError(f"device '{device_name}' not found - rescan")
        if settle:
            # The player was holding a connection a moment ago and this
            # account has one. The panel goes on counting a closed session for
            # a few seconds, and everything below - the receiver, then the
            # converter - is refused for exactly that long. Let it notice.
            log.info("cast: letting the provider release the connection")
            time.sleep(4)
        cc.wait(timeout=10)
        mc = cc.media_controller
        # Attach the watcher BEFORE handing anything over, so the receiver's
        # first verdict is caught too - and it keeps reporting for the whole
        # life of the cast, which the old six-second poll did not.
        self._watch(cc, mc, device_name, self)
        self.active = cc
        # A fresh cast: nothing has been reported yet, and if it starts part
        # way in that offset is what the receiver's own zero means.
        self.last_position, self.position_offset = 0.0, 0.0
        self.duration = duration

        # Straight to the converter when this device has already refused these
        # codecs once - the answer is not going to be different this time, and
        # the twenty seconds of refusals are pure waiting.
        codecs = [c.lower() for c in (known_codecs or [])]
        seen_bad = self._refused.get(device_name, set())
        # A chosen audio or subtitle track can only be honoured by converting:
        # the receiver plays whatever the stream hands it and renders no
        # subtitle carried inside one. Leaving both on their default is what
        # keeps a cast native, which is why the default is a default.
        if audio is not None or subs is not None:
            log.info("cast: a track was chosen - converting so it can be used")
            if self._bridge_cast(mc, device_name, source or url, codecs,
                                 title, audio, subs, start):
                return device_name
            raise RuntimeError("the chosen track could not be cast")
        if ((codecs and seen_bad.intersection(codecs))
                or (device_name, url) in self._needs_bridge):
            if self._bridge_cast(mc, device_name, source or url, codecs,
                                 title, start=start):
                return device_name
            raise RuntimeError(self._no_decoder(codecs))

        # First attempt: the address the stream really lives at.
        #
        # This is the one that works. Side-by-side logs of a channel that
        # casts and one that does not both show the panel's own address being
        # refused outright - only the resolved CDN address ever reaches
        # BUFFERING. So resolve first and go straight to it, rather than
        # spending six seconds proving again that the panel URL is no good.
        #
        # The connection this costs is not what stops a cast either: the same
        # logs show a channel starting to play immediately after the probe.
        resolved, served = _resolve_redirects(url)
        # The server's own type wins; otherwise guess from whichever address
        # still HAS an extension - the resolved one usually does not.
        ctype = served or cast_content_type(
            resolved if "." in resolved.rsplit("/", 1)[-1] else url)
        if ctype in _UNPLAYABLE:
            # Matroska and raw MPEG-TS are not on the Cast platform's list at
            # all, so there is nothing to attempt natively - the receiver would
            # sit at IDLE/ERROR and never say why. But the container is a
            # separate question from the codecs inside it: ffmpeg repackages
            # both into fragmented MP4 without touching a frame of video, and
            # then it plays. Straight to the converter.
            log.info("cast: %s is %s, which no Chromecast plays - "
                     "repackaging it here", device_name, ctype)
            if self._bridge_cast(mc, device_name, source or url, codecs,
                                 title, start=start):
                return device_name
            raise RuntimeError(
                f"this stream is {ctype}, and repackaging it here did not "
                f"help either")
        log.info("cast -> %s: %s (%s)", device_name, resolved,
                 ctype if served else f"{ctype}, guessed")
        verdict = self._play_and_verify(mc, resolved, ctype, title,
                                        start=start)
        if verdict:
            log.info("cast: %s is playing the provider's own stream - "
                     "nothing converted", device_name)
        # None means the receiver has not answered yet. Leave it alone: a
        # second load would replace a cast that is merely slow to start, and
        # that is a channel killed by the retry meant to save it.
        if verdict is not False or resolved == url:
            return device_name

        # Straight to the converter now. The panel's own address used to get
        # its own attempt here, and with this provider it has never once
        # worked - while every attempt opens the stream again, and an account
        # with a single connection is refused for as long as the panel keeps
        # counting the last one. Spending an open on something that has never
        # worked is what left nothing for the converter.
        log.info("cast: %s refused that address", device_name)
        # Both addresses refused. Now - and only now - work out what is
        # actually inside the stream. What mpv reports is worth more than our
        # own parsing: it is decoding the very same channel, and its answer
        # costs nothing. Reading the transport stream is the fallback for when
        # the channel is not playing locally.
        codecs = codecs or [c.lower() for c in _probe_codecs(resolved)]
        log.info("cast: the stream contains %s",
                 ", ".join(codecs) if codecs else "something we could not "
                 "identify - converting it anyway")

        # Third attempt: give the receiver something it CAN decode. This does
        # not wait to be told what the problem is. Knowing the codecs only
        # decides whether the video can be copied through; not knowing them is
        # no reason to stop, because by here the device has refused the stream
        # twice and converting is the only thing left to try.
        if self._bridge_cast(mc, device_name, source or url, codecs, title,
                             start=start):
            return device_name

        # Last resort: the panel's own address, letting the receiver follow
        # the redirect itself. Kept for providers that behave the other way
        # round, but tried only once everything else has failed.
        fallback = cast_content_type(url)
        if fallback not in _UNPLAYABLE:
            log.info("cast -> %s: %s (%s, guessed)", device_name, url,
                     fallback)
            if self._play_and_verify(mc, url, fallback, title,
                                     start=start) is not False:
                return device_name
        raise RuntimeError(self._no_decoder(codecs))

    @staticmethod
    def _no_decoder(codecs: list[str]) -> str:
        bad = " + ".join(c for c in codecs if c not in _CAST_CODECS)
        if not bad:
            return ("the Chromecast refused this stream, and converting it "
                    "here did not help either")
        return f"this channel is {bad}, and converting it here did not help"

    def _bridge_cast(self, mc, device_name: str, url: str,
                     codecs: list[str], title: str,
                     audio: dict | None = None, subs: dict | None = None,
                     start: float = 0.0) -> bool:
        """Convert the stream here and cast that instead.

        ffmpeg copies the video through untouched and re-encodes only what the
        receiver could not take - almost always just the audio - and the
        result is served on the LAN. The panel address is the source, not the
        resolved one: it is the address that plays reliably in every other
        player, ffmpeg included.

        Only ever reached after the device itself has refused the stream, so a
        receiver that decodes E-AC-3 keeps getting it straight from the
        provider and never touches this.
        """
        bad = [c for c in codecs if c not in _CAST_CODECS]
        # Only a refusal teaches the memory. Converting because a track was
        # chosen says nothing about what the device can decode, and recording
        # it would send every later cast of this channel through ffmpeg for no
        # reason at all.
        if audio is None and subs is None:
            self._refused.setdefault(device_name, set()).update(bad)
            self._needs_bridge.add((device_name, url))
        if not CastBridge.available():
            raise RuntimeError(
                f"this Chromecast has no decoder for this channel "
                f"({' + '.join(bad) or 'unknown codecs'}) - install ffmpeg "
                f"to convert it here")
        if audio is None and subs is None:
            log.info("cast: %s cannot decode %s - converting it here",
                     device_name, " + ".join(bad) or "this stream")
        bridged = self.bridge.start(
            url, codecs,
            audio=(audio or {}).get("index", 0),
            subs=None if subs is None else subs.get("index"),
            sub_codec=(subs or {}).get("codec", ""), start_at=start)
        # ffmpeg does the seeking, so the converted stream starts at zero and
        # the offset is added back when the position is read.
        self.position_offset = start
        if self._play_and_verify(mc, bridged, "video/mp4", title) is not False:
            log.info("cast: %s is playing the converted stream", device_name)
            return True
        self.bridge.stop()
        return False

    # How long to wait for the receiver's verdict before letting the cast be.
    # Long enough for a slow provider to get going: the wait ends the moment
    # the state turns, so it only ever costs time on a stream in trouble.
    VERDICT_WAIT = 12.0

    def _play_and_verify(self, mc, url: str, ctype: str, title: str,
                         wait: float | None = None,
                         start: float = 0.0) -> bool | None:
        """Hand the stream over and wait for the receiver to pass judgement.

        True when it took the stream, False when it REFUSED it, and None when
        it said nothing either way. The difference between the last two is
        what keeps a second attempt from doing harm: loading again replaces
        whatever the receiver is doing, so a cast that was merely slow to
        start would be killed by the retry. Only an explicit refusal is worth
        acting on; silence means keep waiting, and the watcher will report
        whatever happens next.
        """
        mc.play_media(url, ctype, title=title or "dopeIPTV",
                      current_time=start or None)
        mc.block_until_active(timeout=10)
        deadline = time.monotonic() + (
            self.VERDICT_WAIT if wait is None else wait)
        while time.monotonic() < deadline:
            time.sleep(0.3)
            state = getattr(mc.status, "player_state", "?")
            why = getattr(mc.status, "idle_reason", None)
            if state in ("PLAYING", "BUFFERING"):
                return True
            # INTERRUPTED is our own load replacing an earlier one - it says
            # nothing about the stream, so it is not a refusal.
            if state == "IDLE" and why in ("ERROR", "CANCELLED"):
                return False
        return None

    @staticmethod
    def _watch(cc, mc, device_name: str, manager=None) -> None:
        if getattr(cc, "_dope_watch", None) is not None:
            return
        watch = _CastWatch(device_name, manager)
        try:
            mc.register_status_listener(watch)
            cc.socket_client.register_connection_listener(watch)
            cc._dope_watch = watch
        except Exception as e:
            log.debug("cast: could not attach the watcher (%s)", e)

    def pause(self) -> None:
        """Freeze the picture on the TV, if the receiver allows it at all.

        A stream announced as live often refuses: there is nothing buffered
        ahead to come back to. That refusal is not an error worth showing -
        the app's own timeshift is what actually makes a live pause work, and
        it does not depend on this succeeding.
        """
        cc = self.active
        if cc is None:
            return
        try:
            cc.media_controller.pause()
        except Exception as e:
            log.info("cast: the receiver would not pause (%s)", e)

    def resume(self) -> None:
        cc = self.active
        if cc is None:
            return
        try:
            cc.media_controller.play()
        except Exception as e:
            log.info("cast: the receiver would not resume (%s)", e)

    def stop(self) -> None:
        # Tell the TV first, tear our own machinery down afterwards.
        #
        # On app close this runs in a daemon thread racing os._exit, with
        # about a second and a half before the process is gone. Stopping the
        # bridge first spent that budget waiting on ffmpeg and on the HTTP
        # server - so the STOP never reached the receiver and the cast simply
        # carried on after the app had quit.
        with self._lock:
            if self.active:
                try:
                    self.active.media_controller.stop()
                except Exception:
                    pass
                self.active = None
        # The bridge goes down with the cast either way - otherwise ffmpeg
        # keeps reading the channel and holds a provider connection for a
        # stream nobody is watching.
        self.bridge.stop()

    def shutdown(self) -> None:
        # Say so. This is the one stop nobody can see happen - the window is
        # already gone and the process is about to be - so without a line here
        # a cast still playing on the TV afterwards cannot be told from one
        # the app never tried to stop.
        if self.active is not None:
            log.info("cast: the app is closing - stopping the cast on %s",
                     getattr(self.active, "name", "?"))
        self.stop()
        with self._lock:
            self._tear_down()  # devices before browser - see _tear_down


class CastDialog(QDialog):
    """Scan for Chromecast devices and cast a stream to one."""

    def __init__(self, window: object, url: str, title: str,
                 codecs: list[str] | None = None,
                 audio_index: int = 0, start: float = 0.0,
                 tracks: dict | None = None, probe: bool = True,
                 source: str | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.url = url
        self.stream_title = title
        # What mpv says the channel is, when it happens to be playing here.
        # Only ever used to explain a refusal - never to refuse in advance:
        # AC-3 plays fine on an Ultra or a Google TV.
        self.codecs = codecs or []
        # The audio track the app is playing. If you switched language here,
        # that is the one you meant to send - so the dialog opens on it. Zero
        # is the stream's own default, and leaving it there is what keeps the
        # cast native.
        self.audio_index = audio_index
        # Where to start. The app already asked whether to resume, so this is
        # a decision already made - the TV just has to honour it.
        self.start = start
        self.duration = 0.0
        # What the player already knows. Free, and it saves opening the
        # stream a second time on an account that has one connection.
        self.tracks = tracks or {}
        # Whether asking the provider directly is worth anything right now.
        self.probe = probe
        # What the converter should read. The same stream in the format the
        # player uses, which is not always the one the receiver is offered.
        self.source = source or url
        self.setWindowTitle(tr("cast_title"))
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        self.status = QLabel(tr("cast_scanning"))
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._cast())
        lay.addWidget(self.list, 1)

        # Audio and subtitle choice. Both are honoured by converting the
        # stream here, because the receiver plays whatever the stream hands it
        # and renders no subtitle that is carried inside one - so leaving both
        # on their default is what keeps a cast native.
        self.audio_box = QComboBox()
        self.subs_box = QComboBox()
        for box, label in ((self.audio_box, tr("cast_audio")),
                           (self.subs_box, tr("cast_subtitles"))):
            row = QHBoxLayout()
            cap = QLabel(label)
            cap.setMinimumWidth(80)
            row.addWidget(cap)
            box.addItem(tr("cast_reading_tracks"), None)
            box.setEnabled(False)
            row.addWidget(box, 1)
            lay.addLayout(row)
        self.track_note = QLabel(tr("cast_track_note"))
        self.track_note.setWordWrap(True)
        self.track_note.setStyleSheet("font-size:11px; opacity:0.7;")
        self.track_note.hide()
        lay.addWidget(self.track_note)
        self.audio_box.currentIndexChanged.connect(self._track_changed)
        self.subs_box.currentIndexChanged.connect(self._track_changed)

        btns = QHBoxLayout()
        self.rescan_btn = QPushButton(tr("cast_rescan"))
        self.cast_btn = QPushButton(tr("cast_cast"), objectName="Primary")
        self.stop_btn = QPushButton(tr("cast_stop"))
        close_btn = QPushButton(tr("common_close"))
        for b in (self.rescan_btn, self.cast_btn, self.stop_btn, close_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

        self.rescan_btn.clicked.connect(self._scan)
        self.cast_btn.clicked.connect(self._cast)
        self.stop_btn.clicked.connect(self._stop)
        close_btn.clicked.connect(self.accept)
        self._scan()
        self._load_tracks()

    # -- audio / subtitle tracks -------------------------------------------

    def _load_tracks(self) -> None:
        """What is in the stream - from the player if it is playing it, and
        only otherwise from ffprobe, which costs a connection."""
        if self.tracks or not self.probe:
            self._fill_tracks(self.tracks)
            return
        def done(tracks):
            try:
                self._fill_tracks(tracks)
            except RuntimeError:
                pass                       # dialog closed while probing

        run_async(self.window.pool, lambda: probe_tracks(self.url), done,
                  lambda _msg: self._fill_tracks({}))

    @staticmethod
    def _track_label(t: dict) -> str:
        bits = [b for b in (t.get("lang"), t.get("title")) if b]
        bits.append(t.get("codec") or "?")
        return " · ".join(bits)

    def _fill_tracks(self, tracks: dict) -> None:
        self.duration = float((tracks or {}).get("duration") or 0.0)
        audio = (tracks or {}).get("audio") or []
        subs = (tracks or {}).get("subtitle") or []
        self.audio_box.blockSignals(True)
        self.subs_box.blockSignals(True)
        self.audio_box.clear()
        self.subs_box.clear()
        self.audio_box.addItem(tr("cast_track_default"), None)
        self.subs_box.addItem(tr("cast_subs_off"), None)
        for t in audio:
            self.audio_box.addItem(self._track_label(t), t)
        for t in subs:
            self.subs_box.addItem(self._track_label(t), t)
        # Open on the track the app is playing. Row 0 is "Default", so track
        # N sits at N+1 - and track 0 IS the default, which is left alone so
        # that a cast nobody changed anything about stays native.
        if 0 < self.audio_index < len(audio):
            self.audio_box.setCurrentIndex(self.audio_index + 1)
        self.audio_box.blockSignals(False)
        self.subs_box.blockSignals(False)
        self._track_changed()
        # A single audio track is no choice at all, and no subtitles means
        # nothing to pick from - leave those boxes out of the way.
        self.audio_box.setEnabled(len(audio) > 1)
        self.subs_box.setEnabled(bool(subs))

    def _track_changed(self) -> None:
        self.track_note.setVisible(self._chosen() != (None, None))

    def _chosen(self) -> tuple[dict | None, dict | None]:
        return self.audio_box.currentData(), self.subs_box.currentData()

    def _set_status(self, text: str) -> None:
        try:
            self.status.setText(text)
        except RuntimeError:
            pass

    def _scan(self) -> None:
        # Fill the list from last time's result immediately. Discovery takes
        # several seconds, and staring at an empty box while the device you
        # cast to yesterday is right there is just waiting for nothing. The
        # scan still runs and replaces this the moment it lands.
        if self.list.count() == 0:
            remembered = self.window.settings.value("cast_devices", "") or ""
            for name in [n for n in remembered.split("\n") if n]:
                self.list.addItem(name)
            if self.list.count():
                self.list.setCurrentRow(0)
        self._set_status(tr("cast_scanning"))
        self.rescan_btn.setEnabled(False)

        def done(names):
            try:
                self.rescan_btn.setEnabled(True)
                self.window.settings.setValue(
                    "cast_devices", "\n".join(names or []))
                self.list.clear()
                for name in names or []:
                    self.list.addItem(name)
                self._set_status(
                    tr("cast_devices_found", n=len(names)) if names
                    else tr("cast_none_found"))
                if names:
                    self.list.setCurrentRow(0)
            except RuntimeError:
                pass

        def fail(msg):
            try:
                self.rescan_btn.setEnabled(True)
            except RuntimeError:
                return
            self._set_status(tr("cast_scan_failed", msg=msg))

        run_async(self.window.pool, self.window.cast.scan, done, fail)

    def _cast(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        name = item.text()
        self._set_status(tr("cast_starting", name=name))

        # Free the provider connection BEFORE the receiver goes for the
        # stream, not after it succeeds. On a single-connection account the
        # old order could not work: the local player still held the one
        # connection, the provider refused the Chromecast's request, and the
        # receiver reported IDLE/ERROR - after which we dutifully stopped
        # local playback, having already lost the cast. Stopping first costs
        # nothing on an account with room to spare.
        stop = getattr(self.window, "stop_local_playback_for_cast", None)
        settle = bool(callable(stop) and stop())

        # The cast strip in the detail pane is the only thing that says a cast
        # is running once this dialog is closed - local playback has stopped,
        # so the player pane is just black.
        def done(n):
            self._set_status(tr("cast_casting_to", name=n))
            self._banner(n, self.stream_title)

        def failed(msg):
            self._set_status(tr("cast_failed", msg=msg))
            self._banner(None, "")

        audio, subs = self._chosen()
        run_async(self.window.pool,
                  lambda: self.window.cast.cast(name, self.url,
                                                 self.stream_title,
                                                 self.codecs, audio, subs,
                                                 self.start, self.duration,
                                                 settle, self.source),
                  done, failed)

    def _banner(self, device: str | None, title: str) -> None:
        show = getattr(self.window, "show_cast_strip", None)
        if callable(show):
            show(device, title)

    def _stop(self) -> None:
        self._banner(None, "")
        run_async(self.window.pool, self.window.cast.stop,
                  lambda _: self._set_status(tr("cast_stopped")),
                  lambda msg: self._set_status(tr("cast_stop_failed", msg=msg)))
