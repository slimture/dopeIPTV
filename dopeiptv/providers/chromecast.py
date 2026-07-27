"""Chromecast discovery and casting (optional, via pychromecast)."""

from __future__ import annotations

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
            r.close()
            if not loc:
                break
            final = requests.compat.urljoin(final, loc)
        if final != url:
            log.info("cast: resolved redirect -> %s", final)
        # text/html is the panel's error page, never a media type - ignore it.
        if ctype.startswith("text/"):
            ctype = ""
        return final, (ctype or None)
    except Exception as e:
        log.debug("cast: redirect resolve failed (%s); using original URL", e)
        return url, None


class ChromecastManager:
    """Discovers Chromecast devices on the LAN and casts streams."""

    def __init__(self) -> None:
        self.devices: list = []
        self.active = None
        self._browser = None

    @staticmethod
    def available() -> bool:
        return _pc() is not None

    def scan(self) -> list[str]:
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
            self._browser = None
        devices, browser = _pc().get_chromecasts(timeout=6)
        self._browser = browser
        self.devices = devices
        return sorted(cc.name for cc in devices)

    def cast(self, device_name: str, url: str, title: str) -> str:
        cc = next((c for c in self.devices if c.name == device_name), None)
        if cc is None:
            raise RuntimeError(f"device '{device_name}' not found - rescan")
        cc.wait(timeout=10)
        mc = cc.media_controller
        original = url
        url, served = _resolve_redirects(url)
        # The server's own type wins; otherwise guess from the address that
        # still HAS an extension - the resolved one usually does not.
        ctype = served or cast_content_type(
            url if "." in url.rsplit("/", 1)[-1] else original)
        # Log what we hand the receiver. A Chromecast that rejects a stream
        # simply shows nothing - no error reaches us - so without this line
        # there is no way to tell "we sent the wrong thing" from "the receiver
        # refused it". Note the receiver supports neither raw MPEG-TS nor
        # Matroska: a .ts or .mkv URL can never produce a picture, whatever we
        # label it.
        log.info("cast -> %s: %s (%s)", device_name, url, ctype)
        mc.play_media(url, ctype, title=title or "dopeIPTV")
        mc.block_until_active(timeout=10)
        # Sample the receiver for a few seconds, not once. Right after the
        # session goes active it is always IDLE with no reason - it has not
        # fetched the manifest yet - so a single read says nothing. The verdict
        # arrives a second or two later: BUFFERING/PLAYING means it took the
        # stream, IDLE with reason ERROR means it fetched and refused, and IDLE
        # with no reason throughout means it never got anything back at all
        # (unreachable host, blocked name).
        try:
            seen = ""
            for _ in range(12):
                time.sleep(0.5)
                st = getattr(mc.status, "player_state", "?")
                why = getattr(mc.status, "idle_reason", None)
                cur = f"{st}/{why}"
                if cur != seen:
                    seen = cur
                    log.info("cast receiver state: %s (idle reason: %s)",
                             st, why)
                if st in ("PLAYING", "BUFFERING") or why:
                    break
        except Exception as e:
            log.debug("cast status poll failed: %s", e)
        self.active = cc
        return device_name

    def stop(self) -> None:
        if self.active:
            try:
                self.active.media_controller.stop()
            except Exception:
                pass
            self.active = None

    def shutdown(self) -> None:
        self.stop()
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
        for cc in self.devices:
            try:
                cc.disconnect(timeout=2)
            except Exception:
                pass


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
        self._set_status(tr("cast_scanning"))
        self.rescan_btn.setEnabled(False)

        def done(names):
            try:
                self.rescan_btn.setEnabled(True)
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

        def done(n):
            self._set_status(tr("cast_casting_to", name=n))

        run_async(self.window.pool,
                  lambda: self.window.cast.cast(name, self.url,
                                                 self.stream_title),
                  done,
                  lambda msg: self._set_status(tr("cast_failed", msg=msg)))

    def _stop(self) -> None:
        run_async(self.window.pool, self.window.cast.stop,
                  lambda _: self._set_status(tr("cast_stopped")),
                  lambda msg: self._set_status(tr("cast_stop_failed", msg=msg)))
