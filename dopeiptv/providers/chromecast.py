"""Chromecast discovery and casting (optional, via pychromecast)."""

from __future__ import annotations


from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout,
)

from ..i18n import tr
from ..core.workers import run_async

try:
    import pychromecast as _pychromecast
except Exception:
    _pychromecast = None


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


class ChromecastManager:
    """Discovers Chromecast devices on the LAN and casts streams."""

    def __init__(self) -> None:
        self.devices: list = []
        self.active = None
        self._browser = None

    @staticmethod
    def available() -> bool:
        return _pychromecast is not None

    def scan(self) -> list[str]:
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
            self._browser = None
        devices, browser = _pychromecast.get_chromecasts(timeout=6)
        self._browser = browser
        self.devices = devices
        return sorted(cc.name for cc in devices)

    def cast(self, device_name: str, url: str, title: str) -> str:
        cc = next((c for c in self.devices if c.name == device_name), None)
        if cc is None:
            raise RuntimeError(f"device '{device_name}' not found - rescan")
        cc.wait(timeout=10)
        mc = cc.media_controller
        mc.play_media(url, cast_content_type(url), title=title or "dopeIPTV")
        mc.block_until_active(timeout=10)
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

        def done(n):
            self._set_status(tr("cast_casting_to", name=n))
            # Hand the single connection to the Chromecast: stop local playback
            # so a single-connection account isn't asked for two streams.
            stop = getattr(self.window, "stop_local_playback_for_cast", None)
            if callable(stop):
                stop()

        run_async(self.window.pool,
                  lambda: self.window.cast.cast(name, self.url,
                                                 self.stream_title),
                  done,
                  lambda msg: self._set_status(tr("cast_failed", msg=msg)))

    def _stop(self) -> None:
        run_async(self.window.pool, self.window.cast.stop,
                  lambda _: self._set_status(tr("cast_stopped")),
                  lambda msg: self._set_status(tr("cast_stop_failed", msg=msg)))
