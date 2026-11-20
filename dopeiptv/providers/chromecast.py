"""Chromecast discovery and casting (optional, via pychromecast)."""

from __future__ import annotations

import threading
import time

from ..core.log import log
from ..core.privacy import url_for_log
from ..i18n import LocalizedError
from .cast_bridge import (
    QUALITY, SAFE_AUDIO, SAFE_VIDEO, CastBridge,
    normalise_quality,
)
from .cast_feed import is_loopback as _is_loopback

# pychromecast drags in zeroconf + ifaddr (~130 ms of the app's startup),
# and this module sits on the main window's import chain - so the import is
# deferred until casting is actually used (the Cast menu action).
_pychromecast = None
_pc_checked = False
_pc_error = ""


def _pc():
    """Import pychromecast on first use; None when it isn't usable."""
    global _pychromecast, _pc_checked, _pc_error
    if not _pc_checked:
        _pc_checked = True
        try:
            import pychromecast
            _pychromecast = pychromecast
        except ModuleNotFoundError as e:
            _pc_error = ""              # simply not installed
            log.info("cast: pychromecast is not installed (%s)", e)
        except Exception as e:
            # Installed, but something underneath it is not: a zeroconf that
            # does not match, a system library missing. Telling someone to
            # install a package they already have sends them on the wrong
            # errand entirely, so say what actually happened.
            _pc_error = f"{type(e).__name__}: {e}"
            log.warning("cast: pychromecast is installed but would not "
                        "import - %s", _pc_error)
    return _pychromecast


def cast_import_error() -> str:
    """Why casting is unavailable, when the reason is not "not installed"."""
    _pc()
    return _pc_error


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
        from ..core import dest_guard
        from ..core._lazy_requests import requests
        # Ask for the redirect only - never open the stream itself. Fetching
        # it (allow_redirects=True) made the panel reset the connection: it
        # costs one of the account's simultaneous connections, and this runs
        # while the receiver is about to take the very same slot. A player
        # User-Agent matters too - panels routinely refuse python-requests.
        headers = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}
        # A catch-up playlist is BUILT by the request for it: one segment per
        # minute asked for, assembled while the connection waits. A deep jump
        # takes the panel well past a short timeout, and timing out handed
        # the receiver a URL that was still baking - it followed it, waited
        # its own few seconds, and gave up with IDLE/ERROR. Waiting here is
        # what makes the receiver's own fetch afterwards instant.
        read = 75 if ("/timeshift/" in url or "timeshift.php" in url) else 8
        final, ctype = url, ""
        for _ in range(4):          # follow a short chain, never a loop
            r = requests.get(final, headers=headers, timeout=(3.05, read),
                             allow_redirects=False, stream=True)
            loc = r.headers.get("Location")
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
            if not loc:
                _log_playlist_head(r)
                r.close()
                break
            r.close()
            nxt = requests.compat.urljoin(final, loc)
            # Each hop as the app's own requests take one (core/dest_guard):
            # a provider's redirect never sends this request to this
            # computer, a link-local host or - from the internet - the home
            # network. Refused, the receiver is handed what was asked for.
            why = dest_guard.redirect_refusal(final, nxt)
            if why:
                log.warning("cast: a redirect to %s was not followed", why)
                return url, None
            final = nxt
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

    def __init__(self, name: str, manager=None, mc=None) -> None:
        self.name = name
        self.manager = manager
        self.mc = mc
        self._last = ""
        self._subs_done = False

    def new_media_status(self, status) -> None:
        # Where the TV has got to. It is the only place that number exists -
        # the sender is not playing anything - and it is what a resume point
        # is made of.
        if self.manager is not None:
            try:
                pos = float(getattr(status, "current_time", 0) or 0)
                if pos > 0:
                    self.manager.last_position = pos
                    # When it was true, so the seconds in between can be
                    # counted here. The receiver reports when it feels like
                    # it - on a state change, and otherwise every few
                    # seconds or not at all - and reading only what it says
                    # left the clock frozen on a film and jumping five
                    # seconds at a time on a channel.
                    self.manager.position_at = time.monotonic()
            except (TypeError, ValueError):
                pass
        cur = (f"{getattr(status, 'player_state', '?')}/"
               f"{getattr(status, 'idle_reason', None)}")
        if self.manager is not None:
            # Kept so the app can tell a cast that ENDED from one that is
            # merely between fragments. An archive playlist is finite - it
            # stops when it catches up with now - and something has to notice
            # that and ask for the next stretch.
            self.manager.state = cur
        if cur != self._last:
            was, self._last = self._last, cur
            # Every rebuffer, with the one number that says why. A receiver
            # that keeps stalling also keeps redrawing its overlay, which
            # is why the chrome never fades on a stream that cycles - and
            # the cycling has been in every log of this all along.
            bridge = getattr(self.manager, "bridge", None)
            extra = ""
            if cur.startswith("BUFFERING") and was.startswith("PLAYING") \
                    and bridge is not None:
                try:
                    extra = f" - converter is {bridge.lead()}"
                except Exception:
                    extra = ""
            log.info("cast %s: receiver %s%s", self.name, cur, extra)
        self._turn_the_subtitle_on(status)

    def _turn_the_subtitle_on(self, status) -> None:
        """Switch on the subtitle rendition once the receiver has found it.

        It arrives in the playlist as its own track, and the receiver lists
        it - but listing is not showing, and a subtitle somebody chose in
        the dialog must not arrive switched off.

        Done from here rather than straight after handing the stream over,
        because at that moment the receiver has not fetched the manifest and
        knows of no tracks at all: asking then found nothing, every time,
        and said nothing about it either.
        """
        if self._subs_done or self.mc is None:
            return
        # Not from a status that belongs to the stream we just replaced.
        # Changing subtitle mid-film loads a new stream, and the report that
        # arrives first is the old one's farewell - IDLE/INTERRUPTED, still
        # carrying the old track list. Acting on it ticked the job off as
        # done and the new subtitle was never switched on at all.
        if getattr(status, "player_state", "") not in (
                "PLAYING", "BUFFERING", "PAUSED"):
            return
        if self.manager is not None and self.manager.stale(status):
            return                      # still the replaced stream talking
        bridge = getattr(self.manager, "bridge", None)
        if not getattr(bridge, "hls", False) or bridge.subs is None:
            self._subs_done = True
            return
        tracks = getattr(status, "subtitle_tracks", None) or []
        ids = [t.get("trackId") for t in tracks
               if isinstance(t, dict) and t.get("type") == "TEXT"
               and t.get("trackId") is not None]
        if not ids:
            return                      # not parsed yet; asked again next time
        self._subs_done = True
        # activeTrackIds lists EVERY active track, audio included - so a
        # cast whose audio was track 1 reported "already on" and the
        # subtitle stayed off, with the log cheerfully agreeing. Only a
        # TEXT track counts as the subtitle being on.
        active = set(getattr(status, "current_subtitle_tracks", None) or [])
        if active & set(ids):
            log.info("cast %s: the subtitle is already on (%s)",
                     self.name, sorted(active & set(ids)))
            return
        # Off this thread. This runs on pychromecast's own receive thread,
        # and enable_subtitle sends a message and then waits for the answer
        # - which can only arrive on the thread it is blocking. It locked
        # itself out and gave up ten seconds later, every time:
        #   could not turn the subtitle on (Execution of enable subtitle
        #   timed out after 10.0 s.)
        mc, name, track = self.mc, self.name, ids[0]

        def ask():
            try:
                mc.enable_subtitle(track)
                log.info("cast %s: turned the subtitle on (track %s of %s)",
                         name, track, ids)
            except Exception as e:
                log.info("cast %s: could not turn the subtitle on (%s)",
                         name, e)

        threading.Thread(target=ask, daemon=True).start()

    def load_media_failed(self, queue_item_id, error_code) -> None:
        log.info("cast %s: receiver refused the stream (error %s)",
                 self.name, error_code)

    def new_connection_status(self, status) -> None:
        log.info("cast %s: sender connection %s",
                 self.name, getattr(status, "status", status))


def _host_of(cc) -> str:
    info = getattr(cc, "cast_info", None)
    host = getattr(info, "host", None) or getattr(cc, "host", None)
    if not host:
        sock = getattr(cc, "socket_client", None)
        host = getattr(sock, "host", None)
    return str(host or "")


def _uuid_of(h: dict):
    """The device's own id when its eureka_info gave one, else one made
    from its address (pychromecast needs an id to tell devices apart)."""
    import uuid
    try:
        return uuid.UUID(str(h.get("uuid") or "").replace("-", ""))
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, "chromecast://" + str(h.get("host")))


class ChromecastManager:
    """Discovers Chromecast devices on the LAN and casts streams."""

    def __init__(self) -> None:
        self.devices: list = []
        self.active = None
        self._browser = None
        # Chromecasts known by their address (added by hand, or found on
        # another part of the network, where mDNS does not reach): each a
        # {"host", "port", "name", "uuid", "model"}, set by set_hosts and
        # listed after every scan.
        self.by_host: list[dict] = []
        # The device objects made for them (_add_hosts).
        self._hosted: list = []
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
        # When last_position was reported, and whether the picture has been
        # moving since. Between two reports the clock is carried by this.
        self.position_at = 0.0
        self.state = ""
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

    def labels(self) -> dict:
        """Each device under the name the list shows it by: its own - or,
        when two devices answer to one name, the name with the address, so
        that neither stands in for the other (anything on the network can
        announce itself as "Living room TV")."""
        by_name: dict[str, list] = {}
        for c in list(self.devices):
            by_name.setdefault(c.name, []).append(c)
        out: dict = {}
        for name, group in by_name.items():
            if len(group) == 1:
                out[name] = group[0]
                continue
            for c in group:
                label, n = f"{name} ({_host_of(c) or '?'})", 2
                while label in out:
                    label, n = f"{name} ({_host_of(c) or '?'}) #{n}", n + 1
                out[label] = c
        return out

    def label_of(self, cc) -> str:
        """The name *cc* has in the list now ("" when it is not in it)."""
        return next((label for label, c in self.labels().items() if c is cc), "")

    def find(self, name: str) -> bool:
        """Whether *name* is a device here - looked for first when this run
        has not met it yet (as cast() does)."""
        with self._lock:
            if self._device(name) is None:
                self.scan()
            return self._device(name) is not None

    def scan(self) -> list[str]:
        with self._lock:
            was_active = getattr(self.active, "name", None)
            was_at = _host_of(self.active) if self.active is not None else ""
            self._tear_down()
            devices, browser = _pc().get_chromecasts(timeout=6)
            self._browser = browser
            self.devices = list(devices)
            self._hosted = []
            self._add_hosts()
            # A running cast survives all this: the receiver plays the stream
            # by itself, so dropping the sender socket does not stop the TV.
            # Re-point 'active' at the freshly discovered object with the same
            # name, or the stop button would have nothing left to talk to -
            # the one at the same address, when two answer to that name.
            if was_active:
                same = [c for c in self.devices if c.name == was_active]
                self.active = next((c for c in same if _host_of(c) == was_at),
                                   same[0] if same else None)
            return sorted(self.labels())

    def set_hosts(self, hosts: list[dict], attach: bool = True) -> list[str]:
        """Chromecasts by their address, listed from now on beside those
        mDNS finds (and after every scan); one no longer known leaves the
        list, unless it is the one casting. Their names in the list.
        *attach*: listed now (else at the next scan)."""
        with self._lock:
            self.by_host = [dict(h) for h in hosts if h.get("host")]
            now = {str(h["host"]) for h in self.by_host}
            for c in list(self._hosted):
                if _host_of(c) not in now and c is not self.active:
                    self._hosted.remove(c)
                    if c in self.devices:
                        self.devices.remove(c)
            return self._add_hosts() if attach else []

    def _add_hosts(self) -> list[str]:
        """A device object for each one known by its address that mDNS did
        not find (same address): made without connecting - cast() does that."""
        pc = _pc()
        if pc is None or not self.by_host:
            return []
        names: list = []
        for h in self.by_host:
            host = str(h["host"])
            cc = next((c for c in self.devices if _host_of(c) == host), None)
            if cc is None:
                name = str(h.get("name") or "").strip() or f"Chromecast ({host})"
                if any(c.name == name for c in self.devices):
                    name = f"{name} ({host})"
                try:
                    cc = pc.get_chromecast_from_host(
                        (host, int(h.get("port") or 8009), _uuid_of(h),
                         h.get("model") or None, name), tries=2, timeout=8)
                except Exception as e:
                    log.info("cast: no Chromecast object for %s (%s)", host, e)
                    continue
                self.devices.append(cc)
                self._hosted.append(cc)
            names.append(cc)
        return [self.label_of(cc) or cc.name for cc in names]

    def _device(self, name: str):
        return self.labels().get(name)

    def stale(self, status) -> bool:
        """Whether *status* is still talking about the stream we replaced."""
        sid = getattr(status, "media_session_id", None)
        return sid is not None and sid == getattr(self, "old_session", None)

    def position(self) -> float:
        """How far into the title the TV has got, in seconds.

        Carried forward from the last report rather than left sitting on
        it. A receiver sends a media status when something happens to it,
        not once a second - so a film that simply plays sends nothing at
        all and the counter stood still, while a channel that reports now
        and then made it jump five seconds and stop again.

        Only while it is actually playing: a paused picture is not getting
        any further in, and neither is one that has stopped.
        """
        at = self.position_offset + self.last_position
        if self.position_at and self.state.startswith("PLAYING"):
            at += max(0.0, time.monotonic() - self.position_at)
        return at

    def cast(self, device_name: str, url: str, title: str,
             known_codecs: list[str] | None = None,
             audio: dict | None = None, subs: dict | None = None,
             start: float = 0.0, duration: float = 0.0,
             settle: bool = False, source: str | None = None,
             quality: str = "original", height: int = 0,
             fps: float = 0.0, dvr: bool = False, feed=None) -> str:
        """*feed*: the archive or the rewind buffer in pieces
        (cast_feed.Feed): converted here into one playlist, so the receiver
        never sees a piece end - nor the buffer's loopback address."""
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
                raise LocalizedError("cast_err_not_found", name=device_name)
        if settle:
            # The player was holding a connection a moment ago and this
            # account has one. The panel goes on counting a closed session for
            # a moment, and everything below - the receiver, then the
            # converter - is refused for exactly that long. Let it notice.
            #
            # Kept short on purpose: this is four seconds of every cast that
            # follows local playback, and a refusal is not fatal any more -
            # the converter tries again by itself, so guessing low costs a
            # retry at worst and saves the wait every other time.
            if self.bridged():
                # Another channel on this cast (a zap): the converter of
                # the last one holds that connection - it goes first.
                log.info("cast: stopping the converter of the last stream")
                self.bridge.stop()
            log.info("cast: letting the provider release the connection")
            time.sleep(1.5)
        if not url:
            # Nothing to hand over. It has happened - a panel reopened on a
            # session whose address had not been recorded - and casting an
            # empty string just produces a mystified receiver.
            raise LocalizedError("cast_err_no_address")
        quality = self._needed_quality(quality, height, fps)
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
        self.position_at = 0.0
        self.state = ""
        # A broadcast has no length, whatever was handed in. Being recorded
        # here is what a dvr cast IS, and a length is what makes the receiver
        # announce the thing as a title with a progress bar over the picture.
        self.duration = 0.0 if dvr else duration

        # Straight to the converter when this device has already refused these
        # codecs once - the answer is not going to be different this time, and
        # the twenty seconds of refusals are pure waiting.
        codecs = [c.lower() for c in (known_codecs or [])]
        seen_bad = self._refused.get(device_name, set())
        # A chosen audio or subtitle track can only be honoured by converting:
        # the receiver plays whatever the stream hands it and renders no
        # subtitle carried inside one. Leaving both on their default is what
        # keeps a cast native, which is why the default is a default.
        if feed is not None:
            log.info("cast: converting - the %s comes in pieces, the "
                     "receiver gets them as one playlist", feed.kind)
            if self._bridge_cast(mc, device_name, source or url, codecs,
                                 title, audio, None, 0.0, quality, feed=feed):
                return device_name
            raise LocalizedError("cast_err_need_ffmpeg_any"
                                 if not CastBridge.available()
                                 else "cast_err_refused")
        # This computer's own address (the rewind buffer's server, a local
        # proxy): no receiver reaches it - only the converter here can.
        local = _is_loopback(source or url) or _is_loopback(url)
        if audio is not None or subs is not None or quality != "original" \
                or dvr or local:
            log.info("cast: converting - %s",
                     "a track was chosen" if audio is not None
                     or subs is not None else
                     f"this device is set to {quality}" if quality != "original"
                     else "the stream is on this computer" if local
                     else "so the picture can be held here")
            if self._bridge_cast(mc, device_name, source or url, codecs,
                                 title, audio, subs, start, quality):
                return device_name
            if local:
                raise LocalizedError("cast_err_need_ffmpeg_any"
                                     if not CastBridge.available()
                                     else "cast_err_refused")
            if audio is not None or subs is not None or quality != "original":
                raise LocalizedError("cast_err_track")
            # Only the pause was riding on the converter. Casting the channel
            # matters more than being able to hold it, so carry on the
            # ordinary way rather than fail outright.
            log.info("cast: the converter would not start - casting %s "
                     "straight through instead", device_name)
        if ((codecs and seen_bad.intersection(codecs))
                or (device_name, url) in self._needs_bridge):
            if self._bridge_cast(mc, device_name, source or url, codecs,
                                 title, start=start):
                return device_name
            raise self._no_decoder(codecs)

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
            raise LocalizedError("cast_err_container", type=ctype)
        log.info("cast -> %s: %s (%s)", device_name, url_for_log(resolved),
                 ctype if served else f"{ctype}, guessed")
        verdict = self._play_and_verify(mc, resolved, ctype, title,
                                        start=start)
        if verdict:
            log.info("cast: %s is playing the provider's own stream - "
                     "nothing converted", device_name)
        if verdict is not False and self.bridged():
            # The receiver reads the provider now: a converter left from
            # the last stream would only hold a connection.
            log.info("cast: the converter of the last stream is not read "
                     "any more - stopping it")
            self.bridge.stop()
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
            log.info("cast -> %s: %s (%s, guessed)", device_name,
                     url_for_log(url), fallback)
            if self._play_and_verify(mc, url, fallback, title,
                                     start=start) is not False:
                return device_name
        raise self._no_decoder(codecs)

    #: CastHub hands this manager the pieces of an archive or a buffer.
    TAKES_FEED = True

    @staticmethod
    def _needed_quality(want: str, height: int, fps: float) -> str:
        """What this particular picture actually needs.

        The device setting is a ceiling, not an instruction. Most channels
        come in three versions - SD, HD and FHD - and only the last one is
        beyond an older receiver; scaling the HD one down would throw away
        picture for nothing. A source already under the ceiling is left alone,
        at the frame rate it came with.

        The ceiling is what the device generation can decode, in lines and
        frames a second - see QUALITY. Nothing under it is touched, so an
        HD channel goes over untouched on every tier and only what is
        genuinely beyond the decoder is adapted.

        So is one whose size we could not find out. Adapting on a guess is the
        worse mistake: it re-encodes HD channels that were perfectly fine, and
        does it invisibly. An older device that stutters can be helped on the
        next attempt, when the picture IS known - the player fills that in
        within a frame or two of starting.
        """
        want = normalise_quality(want)
        if want == "original":
            return want
        if not height:
            log.info("cast: the picture size is not known - sending it as it "
                     "is rather than converting on a guess")
            return "original"
        limit_h, limit_fps = QUALITY.get(want, (0, 0))
        too_big = limit_h and height > limit_h
        too_fast = limit_fps and fps and fps > limit_fps + 1
        if not too_big and not too_fast:
            log.info("cast: %dp%s is within this device's limit - "
                     "sending it as it is", height,
                     f"{fps:g}" if fps else "")
            return "original"
        log.info("cast: %dp%s is beyond this device's limit - sending it as "
                 "%s", height, f"{fps:g}" if fps else "",
                 ChromecastManager.quality_label(want, height, fps))
        return want

    @staticmethod
    def quality_label(want: str, height: int, fps: float) -> str:
        """What a picture this size comes out as, in the same words the
        picture itself is described in - so the menu can say 1080p50 -> 720p50
        rather than naming a setting nobody chose by name."""
        limit_h, limit_fps = QUALITY.get(normalise_quality(want), (0, 0))
        out_h = min(height, limit_h) if limit_h and height else limit_h
        out_fps = min(fps, limit_fps) if limit_fps and fps else fps
        return f"{out_h}p{out_fps:g}" if out_fps else f"{out_h}p"

    @staticmethod
    def _no_decoder(codecs: list[str]) -> LocalizedError:
        bad = " + ".join(c for c in codecs if c not in _CAST_CODECS)
        if not bad:
            return LocalizedError("cast_err_refused")
        return LocalizedError("cast_err_codecs", codecs=bad)

    def _bridge_cast(self, mc, device_name: str, url: str,
                     codecs: list[str], title: str,
                     audio: dict | None = None, subs: dict | None = None,
                     start: float = 0.0, quality: str = "original",
                     feed=None) -> bool:
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
        if audio is None and subs is None and quality == "original":
            self._refused.setdefault(device_name, set()).update(bad)
            self._needs_bridge.add((device_name, url))
        if not CastBridge.available():
            if bad:
                raise LocalizedError("cast_err_need_ffmpeg",
                                     codecs=" + ".join(bad))
            raise LocalizedError("cast_err_need_ffmpeg_any")
        if audio is None and subs is None and quality == "original":
            log.info("cast: %s cannot decode %s - converting it here",
                     device_name, " + ".join(bad) or "this stream")
        bridged = self.bridge.start(
            url, codecs, receiver=_host_of(self.active) if self.active is not None else "",
            audio=(audio or {}).get("index", 0),
            subs=None if subs is None else subs.get("index"),
            sub_codec=(subs or {}).get("codec", ""), start_at=start,
            quality=quality, sub_lang=(subs or {}).get("lang", ""),
            feed=feed)
        # ffmpeg does the seeking, so the converted stream starts at zero and
        # the offset is added back when the position is read.
        self.position_offset = start
        # A text subtitle travels beside the picture as a WebVTT rendition,
        # which means the bridge hands over a playlist rather than one long
        # response - and a receiver told "video/mp4" about a playlist plays
        # nothing at all.
        ctype = ("application/x-mpegURL" if self.bridge.hls else "video/mp4")
        if self._play_and_verify(mc, bridged, ctype, title,
                                 converted=True) is not False:
            log.info("cast: %s is playing the converted stream", device_name)
            return True
        self.bridge.stop()
        return False

    # How long to wait for the receiver's verdict before letting the cast be.
    # Long enough for a slow provider to get going: the wait ends the moment
    # the state turns, so it only ever costs time on a stream in trouble.
    VERDICT_WAIT = 12.0

    def _play_and_verify(self, mc, url: str, ctype: str, title: str,
                         wait: float | None = None, start: float = 0.0,
                         converted: bool = False) -> bool | None:
        """Hand the stream over and wait for the receiver to pass judgement.

        True when it took the stream, False when it REFUSED it, and None when
        it said nothing either way. The difference between the last two is
        what keeps a second attempt from doing harm: loading again replaces
        whatever the receiver is doing, so a cast that was merely slow to
        start would be killed by the retry. Only an explicit refusal is worth
        acting on; silence means keep waiting, and the watcher will report
        whatever happens next.
        """
        # The receiver settled this, in two answers on two runs:
        #
        #   with a title  metadata={'title': 'Slumberland ...'}  bar shown
        #   with none     metadata={}                            bar shown
        #
        # So the bar is not drawn from the metadata. It is drawn from
        # BUFFERED plus a duration: that pair IS the receiver's request for
        # VOD chrome, and it keeps it up. Channels go over the same
        # converter as LIVE with no duration and their picture is clean -
        # which leaves exactly one difference between the clean case and
        # the dirty one, and it is this pair.
        #
        # A converted stream is therefore handed over like a channel. It
        # loses nothing real: an endless fragmented-MP4 pipe cannot be
        # seeked by the receiver anyway, so the bar it drew was a control
        # for something that was not there. The strip here keeps the title,
        # the clock and the seeking - self.duration is untouched by this.
        #
        # A NATIVE film is left alone: the receiver fetches that file
        # itself and really can seek it, so BUFFERED with the length is
        # true, and its chrome is the receiver's own business.
        try:
            self.old_session = getattr(mc.status, "media_session_id", None)
        except Exception:
            self.old_session = None
        seekable = self.duration > 0 and not converted
        log.info("cast: handing over as %s",
                 f"BUFFERED ({self.duration:.0f} s), from {start:.0f} s"
                 if seekable else "LIVE, with nothing to draw")
        if seekable:
            mc.play_media(url, ctype, title=title or "dopeIPTV",
                          current_time=float(start),
                          stream_type="BUFFERED",
                          media_info={"duration": float(self.duration)})
        else:
            # Told where to start when there is a playlist: LIVE with no
            # currentTime means the live edge, and for a growing playlist
            # that is the converter's write head, which it starves at.
            hls = converted and getattr(self.bridge, "hls", False)
            mc.play_media(url, ctype,
                          current_time=start or (0.0 if hls else None),
                          stream_type="LIVE")
        mc.block_until_active(timeout=10)
        self._report_receiver_ui(mc)
        self._ask_for_the_subtitle(mc)
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

    def _report_receiver_ui(self, mc) -> None:
        """Write down what the receiver says it is doing with what we sent.

        Five rounds have gone into guessing which announcement makes the
        on-screen overlay go away, with the television as the only
        instrument and a whole test cycle per guess. The receiver answers
        this itself, in the status it sends back, and nobody has ever
        looked:

          streamType   what it decided this stream IS. It does NOT have to
                       agree with what we asked for - a receiver that finds
                       a duration where LIVE was claimed may reclassify,
                       and if it does, no announcement of ours can win.
          metadata     what it has to draw a card from. Empty means the
                       card is not ours.
          commands     the supportedMediaCommands bitmask. Bit 1 is PAUSE,
                       bit 2 SEEK. A scrubber on screen with SEEK absent
                       means the bar is decoration the receiver drew for
                       its own reasons.

        Off the calling thread: reading status waits for an answer.
        """
        name = getattr(self.active, "name", "")

        def look():
            # Not time.sleep: this is a diagnostic on its own thread, and
            # borrowing the module's sleep makes it indistinguishable from
            # the deliberate waits the cast path takes.
            threading.Event().wait(2.0)   # let it settle on its decision
            try:
                mc.update_status()
            except Exception as e:
                log.info("cast %s: could not read the receiver back (%s)",
                         name, e)
                return
            st = mc.status
            cmds = getattr(st, "supported_media_commands", None)
            log.info("cast %s: the receiver calls it %s, metadata=%r, "
                     "duration=%s, commands=%s%s", name,
                     getattr(st, "stream_type", "?"),
                     getattr(st, "media_metadata", None) or {},
                     getattr(st, "duration", None), cmds,
                     "" if cmds is None else
                     f" (pause={bool(cmds & 1)} seek={bool(cmds & 2)})")

        threading.Thread(target=look, daemon=True).start()

    def _ask_for_the_subtitle(self, mc) -> None:
        """Go and find the subtitle track rather than waiting to be told.

        Waiting was not enough. A receiver reports when something happens
        to it, and the report that carries the track list is one particular
        one - pychromecast keeps the previous list when a status arrives
        without media in it, so a cast that started without subtitles left
        an empty list standing and the listener never saw a text track at
        all. Nothing was switched on and nothing said why.

        So ask. Off the calling thread, because update_status waits for an
        answer, and for a bounded while, because a receiver that has no
        text track is not going to grow one.
        """
        if not getattr(self.bridge, "hls", False) or self.bridge.subs is None:
            return
        name = getattr(self.active, "name", "")

        def look():
            for _ in range(20):
                time.sleep(1.0)
                try:
                    mc.update_status()
                except Exception:
                    continue
                st = mc.status
                if self.stale(st):
                    continue            # the old stream's last word
                tracks = getattr(st, "subtitle_tracks", None) or []
                ids = [t.get("trackId") for t in tracks
                       if isinstance(t, dict) and t.get("type") == "TEXT"
                       and t.get("trackId") is not None]
                if not ids:
                    continue
                on = set(getattr(st, "current_subtitle_tracks", None) or [])
                if on & set(ids):
                    log.info("cast %s: the subtitle is on (%s)",
                             name, sorted(on & set(ids)))
                    return
                try:
                    mc.enable_subtitle(ids[0])
                    log.info("cast %s: turned the subtitle on (track %s "
                             "of %s)", name, ids[0], ids)
                except Exception as e:
                    log.info("cast %s: could not turn the subtitle on (%s)",
                             name, e)
                return
            log.info("cast %s: the receiver never listed a text track - "
                     "the subtitle is in the playlist but it is not "
                     "showing it", name)

        threading.Thread(target=look, daemon=True).start()

    @staticmethod
    def _watch(cc, mc, device_name: str, manager=None) -> None:
        old = getattr(cc, "_dope_watch", None)
        if old is not None:
            # The same device, cast to again: the listener is already
            # attached and stays, but it must forget what the last stream
            # was - not least that it had already turned a subtitle on.
            old.manager, old.mc = manager, mc
            old._subs_done = False
            return
        watch = _CastWatch(device_name, manager, mc)
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

    def seek(self, seconds: float) -> None:
        """Move the picture on the TV to *seconds* into the title."""
        cc = self.active
        if cc is None:
            return
        try:
            cc.media_controller.seek(max(0.0, seconds))
            self.last_position = max(0.0, seconds) - self.position_offset
        except Exception as e:
            log.info("cast: the receiver would not seek (%s)", e)

    def bridged(self) -> bool:
        """Whether what is playing is coming through the converter.

        It matters for seeking: the receiver can seek a file it fetched
        itself, but what the converter serves has no length and no index -
        it is a pipe - so moving inside it means building it again from the
        new point.
        """
        return self.bridge.path is not None

    def resume(self) -> None:
        cc = self.active
        if cc is None:
            return
        try:
            cc.media_controller.play()
        except Exception as e:
            log.info("cast: the receiver would not resume (%s)", e)

    def volume(self) -> tuple[float, bool]:
        """The TV's volume as it stands: level 0-1, and whether it is muted."""
        cc = self.active
        if cc is None:
            return 0.0, False
        try:
            return (float(getattr(cc.status, "volume_level", 0.0) or 0.0),
                    bool(getattr(cc.status, "volume_muted", False)))
        except Exception:
            return 0.0, False

    def set_volume(self, level: float) -> None:
        """Set the TV's volume to *level* (0-1).

        This is the receiver's own volume, not the stream's - the same one the
        TV remote changes - so it survives a track switch and everything else
        that rebuilds the stream underneath it.
        """
        cc = self.active
        if cc is None:
            return
        try:
            cc.set_volume(max(0.0, min(1.0, level)))
        except Exception as e:
            log.info("cast: the receiver would not change volume (%s)", e)

    def set_muted(self, muted: bool) -> None:
        cc = self.active
        if cc is None:
            return
        try:
            cc.set_volume_muted(bool(muted))
        except Exception as e:
            log.info("cast: the receiver would not mute (%s)", e)

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
