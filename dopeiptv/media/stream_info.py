"""A short channel-change banner, fed only by the current mpv core.

The selected video track is read once when mpv reports the requested file
loaded, then again only when mpv says the video format changed
(``video-params``). Both arrive on mpv's event thread and reach the GUI
thread through signals; nothing polls."""
from __future__ import annotations

import weakref

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel

from ..core import stream_info
from ..core.log import log


class StreamInfoMixin:
    def _init_stream_info(self, palette: dict) -> None:
        self._info_palette = palette
        self.stream_info_badge = QLabel("", self)
        self.stream_info_badge.setTextFormat(Qt.TextFormat.PlainText)
        self.stream_info_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.stream_info_badge.hide()
        self._info_hide = QTimer(self)
        self._info_hide.setSingleShot(True)
        self._info_hide.setInterval(4000)
        self._info_hide.timeout.connect(self.stream_info_badge.hide)
        self._info_path = None
        self._info_ready = False
        self._info_shown = False
        self._info_last = {}
        self._info_loaded.connect(self._stream_info_loaded)
        self._info_reconfig.connect(self._stream_info_reconfig)

    def _reset_stream_info(self) -> None:
        self._info_hide.stop()
        self.stream_info_badge.hide()
        self._info_path = None
        self._info_ready = False
        self._info_shown = False
        self._info_last = {}

    def _arm_stream_info(self, core, path: str) -> None:
        """Expect *path* on *core*. Hooks each core once: its file-loaded
        event and its video-params observer, both handed to the GUI thread."""
        if not self._chan_key:
            return
        self._info_path = path
        if getattr(self, "_info_core", lambda: None)() is core:
            return
        me, ref = weakref.ref(self), weakref.ref(core)

        def loaded(_event) -> None:        # mpv's event thread
            player, mpv = me(), ref()
            if player is not None and mpv is not None:
                try:
                    player._info_loaded.emit(str(mpv.path or ""))
                except (RuntimeError, AttributeError):
                    pass

        def reconfigured(_name, _value) -> None:   # mpv's event thread
            player = me()
            if player is not None:
                try:
                    player._info_reconfig.emit()
                except (RuntimeError, AttributeError):
                    pass

        core.event_callback("file-loaded")(loaded)
        try:
            core.observe_property("video-params", reconfigured)
        except Exception as e:
            log.debug("Stream info: format changes not followed: %s", e)
        self._info_core = ref

    def _stream_info_loaded(self, path: str) -> None:
        if not self._info_path or path != self._info_path:
            return
        self._info_ready = True
        self._read_stream_info()

    def _stream_info_reconfig(self) -> None:
        if self._info_ready:
            self._read_stream_info()

    def _read_stream_info(self) -> None:
        """Read the selected video track once and show or update the banner."""
        if not stream_info.enabled(self._settings):
            self._info_hide.stop()
            self.stream_info_badge.hide()
            return
        core = self.video.mpv
        if not self._info_ready or core is None:
            return
        try:
            if str(core.path or "") != self._info_path:
                return
            info = stream_info.observed(core.track_list)
        except Exception:
            return
        if not info:
            return
        if info != self._info_last:
            self._info_last = info
            self.stream_info_changed.emit(self._chan_key, info)
        self.stream_info_badge.setText(stream_info.summary(info))
        if not self.stream_info_badge.isHidden():
            self._place_stream_info()
        if not self._info_shown:
            self._info_shown = True
            self.stream_info_badge.setStyleSheet(
                f"background:{self._info_palette['bg']}; color:{self._info_palette['text']};"
                "border-radius:6px; padding:8px 12px; font-size:13px;")
            self._place_stream_info()
            self.stream_info_badge.show()
            self.stream_info_badge.raise_()
            self._info_hide.start()

    def _place_stream_info(self) -> None:
        g = self._ov_surface.geometry()
        b = self.stream_info_badge
        b.setMaximumWidth(max(1, g.width() - 24))
        b.adjustSize()
        b.move(g.x() + max(0, (g.width() - b.width()) // 2), g.y() + 12)
