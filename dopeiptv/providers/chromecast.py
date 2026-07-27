"""Chromecast discovery and casting (optional, via pychromecast)."""

from __future__ import annotations

import threading
import time

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout,
)

from ..core.log import log
from ..i18n import tr
from ..core.workers import run_async

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


class _CastWatch:
    """Writes down what the receiver and the sender socket do, for the whole
    life of a cast.

    Casting is one-way from here: play_media returns and everything that
    matters afterwards happens on the TV. When a cast dies minutes later there
    is otherwise no way to tell a receiver-side error from our own socket
    quietly going away - so both are logged as they happen.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._last = ""

    def new_media_status(self, status) -> None:
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

    def cast(self, device_name: str, url: str, title: str) -> str:
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
        cc.wait(timeout=10)
        mc = cc.media_controller
        # Attach the watcher BEFORE handing anything over, so the receiver's
        # first verdict is caught too - and it keeps reporting for the whole
        # life of the cast, which the old six-second poll did not.
        self._watch(cc, mc, device_name)
        self.active = cc

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
            # Better a sentence in the dialog than a black TV: this ends in
            # IDLE/ERROR every single time and the receiver never says why.
            raise RuntimeError(
                f"this stream is {ctype}, which a Chromecast cannot play")
        log.info("cast -> %s: %s (%s)", device_name, resolved,
                 ctype if served else f"{ctype}, guessed")
        if self._play_and_verify(mc, resolved, ctype, title):
            return device_name
        if resolved == url:
            return device_name

        # Second attempt: the panel's own address, letting the receiver follow
        # the redirect itself. It has not worked with this provider yet, but
        # it is a different request against a different host and costs only
        # the wait - and by now the cast has failed anyway.
        log.info("cast: %s would not take that address - trying the panel URL",
                 device_name)
        fallback = cast_content_type(url)
        log.info("cast -> %s: %s (%s, guessed)", device_name, url, fallback)
        if fallback not in _UNPLAYABLE:
            self._play_and_verify(mc, url, fallback, title)
        return device_name

    @staticmethod
    def _play_and_verify(mc, url: str, ctype: str, title: str,
                         wait: float = 6.0) -> bool:
        """Hand the stream over and wait for the receiver to pass judgement.

        True when it took the stream, False when it refused it or never said
        anything at all. The wait is what makes a second attempt possible: a
        Chromecast reports a refusal only through its own status - no error
        ever reaches the sender - so without looking there is nothing to act
        on. It costs nothing when the cast works, which is the common case:
        the state turns to BUFFERING within a second or two.
        """
        mc.play_media(url, ctype, title=title or "dopeIPTV")
        mc.block_until_active(timeout=10)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(0.3)
            state = getattr(mc.status, "player_state", "?")
            why = getattr(mc.status, "idle_reason", None)
            if state in ("PLAYING", "BUFFERING"):
                return True
            # INTERRUPTED is our own second load replacing the first - it says
            # nothing about the stream, so it is not a refusal.
            if state == "IDLE" and why in ("ERROR", "CANCELLED"):
                return False
        return False

    @staticmethod
    def _watch(cc, mc, device_name: str) -> None:
        if getattr(cc, "_dope_watch", None) is not None:
            return
        watch = _CastWatch(device_name)
        try:
            mc.register_status_listener(watch)
            cc.socket_client.register_connection_listener(watch)
            cc._dope_watch = watch
        except Exception as e:
            log.debug("cast: could not attach the watcher (%s)", e)

    def stop(self) -> None:
        with self._lock:
            if self.active:
                try:
                    self.active.media_controller.stop()
                except Exception:
                    pass
                self.active = None

    def shutdown(self) -> None:
        self.stop()
        with self._lock:
            self._tear_down()  # devices before browser - see _tear_down


class CastDialog(QDialog):
    """Scan for Chromecast devices and cast a stream to one."""

    def __init__(self, window: object, url: str, title: str) -> None:
        super().__init__(window)
        self.window = window
        self.url = url
        self.stream_title = title
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
        if callable(stop):
            stop()

        # The cast strip in the detail pane is the only thing that says a cast
        # is running once this dialog is closed - local playback has stopped,
        # so the player pane is just black.
        def done(n):
            self._set_status(tr("cast_casting_to", name=n))
            self._banner(n, self.stream_title)

        def failed(msg):
            self._set_status(tr("cast_failed", msg=msg))
            self._banner(None, "")

        run_async(self.window.pool,
                  lambda: self.window.cast.cast(name, self.url,
                                                 self.stream_title),
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
