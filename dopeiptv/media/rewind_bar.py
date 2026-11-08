"""The bars over the video that go back in a live channel.

The DVR row is the rewind buffer - mpv's own, or the app's (DvrSession) -
with LIVE at its end; the timeshift timeline is the provider's archive of
a catch-up channel. On a channel with both they take turns in one place,
switched by the DVR | TIMESHIFT switch at their left (_bars_shared).

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from ..core.local_buffer import (
    LIVE_REOPEN_LAG, BufferWindow, LiveLag, buffer_window,
)
from ..core.log import log
from ..i18n import tr
from .seek_slider import _SeekSlider, _format_time


class _BarSwitch(QWidget):
    """DVR | TIMESHIFT at the left of the bar over the video, for a catch-up
    channel watched live with a rewind buffer: the DVR row (the buffer,
    instant) and the timeshift timeline (the provider's archive) take turns
    in one place instead of lying one above the other. Each bar has its own
    switch with its own side lit, so the two never need keeping in step."""

    picked = pyqtSignal(str)

    _STYLE = (
        "QWidget#BarSwitch { background: rgba(255,255,255,0.10);"
        " border-radius: 6px; }"
        "QPushButton { background: transparent; border: none;"
        " border-radius: 5px; padding: 2px 8px; font-size: 11px;"
        " font-weight: 700; color: rgba(255,255,255,0.62); }"
        "QPushButton:hover { color: #FFFFFF;"
        " background: rgba(255,255,255,0.14); }"
        "QPushButton[lit=\"true\"] { color: #FFFFFF;"
        " background: rgba(255,255,255,0.26); }")

    def __init__(self, lit: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="BarSwitch")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(self._STYLE)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.buttons: dict[str, QPushButton] = {}
        for view in ("dvr", "timeshift"):
            b = QPushButton()
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("lit", view == lit)
            b.clicked.connect(lambda _=False, v=view: self.picked.emit(v))
            lay.addWidget(b)
            self.buttons[view] = b
        self.retranslate()
        self.hide()

    def retranslate(self) -> None:
        dvr, ts = self.buttons["dvr"], self.buttons["timeshift"]
        dvr.setText("DVR")
        dvr.setToolTip(tr("bar_dvr_tip"))
        ts.setText(tr("player_timeshift"))
        ts.setToolTip(tr("bar_timeshift_tip"))


class RewindBarMixin:
    # -- building --------------------------------------------------------------

    def _build_dvr_row(self) -> None:
        self._local_buffer_enabled = False
        self._local_buffer_ready = False
        self._local_buffer_hide_timer = QTimer(self)
        self._local_buffer_hide_timer.setSingleShot(True)
        self._local_buffer_hide_timer.setInterval(2500)
        self._local_buffer_hide_timer.timeout.connect(self._hide_local_buffer)
        self._local_slider_window: BufferWindow | None = None
        self._live_lag = LiveLag()
        # At live, the playhead runs behind the newest data by mpv's forward
        # buffer (the network buffer, ~10 s): that is live, not rewound. The
        # row measures from it - _live_ahead, taken while following live -
        # and _dvr_live is whether the user is following live at all.
        self._dvr_live = True
        self._live_ahead = 0.0
        self._dvr_span = 0.0
        self._local_fs_window: BufferWindow | None = None
        # The DVR row floats over the bottom of the video, like the hover
        # scrubber of a film: shown on pointer movement, gone when idle. In
        # the control bar it was a row of its own that came and went - the
        # docked player changed height under the pointer each time, Qt sent a
        # synthetic mouse move for it, and that brought the row back.
        self.local_buffer_bar = QWidget(self, objectName="DvrOverlay")
        self.local_buffer_bar.setStyleSheet(
            "QWidget#DvrOverlay { background: rgba(16,16,20,215);"
            " border-radius: 8px; }")
        local_layout = QHBoxLayout(self.local_buffer_bar)
        local_layout.setContentsMargins(8, 5, 8, 5)
        local_layout.setSpacing(8)
        self.local_buffer_time = QLabel("")
        self.local_buffer_time.setStyleSheet(
            "color: #ECECF1; font-size: 12px; background: transparent;")
        self.local_buffer_seek = _SeekSlider()
        self.local_buffer_seek.setToolTip(tr("tooltip_timeshift"))
        self.local_buffer_seek.seek_requested.connect(self._seek_local_buffer)
        self.local_buffer_live = QPushButton(tr("mv_live"), objectName="MiniBtn")
        self.local_buffer_live.clicked.connect(self._local_go_live)
        self._label_live_button(force=True)
        self._local_buffer_controls = (
            self.local_buffer_bar, self.local_buffer_seek, self.local_buffer_live,
            self.local_buffer_time)
        for widget in self._local_buffer_controls:
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        # A catch-up channel: this row and the timeshift timeline take turns
        # (_bars_shared); which one shows is _bar_view.
        self._dvr_switch = _BarSwitch("dvr")
        self._dvr_switch.picked.connect(self._pick_bar)
        self._bar_view = self._chosen_bar()
        local_layout.addWidget(self._dvr_switch)
        local_layout.addWidget(self.local_buffer_time)
        local_layout.addWidget(self.local_buffer_seek, 1)
        local_layout.addWidget(self.local_buffer_live)
        self.local_buffer_bar.hide()

    def _build_ts_timeline(self) -> None:
        # Live timeline for timeshift channels: a floating bar at the bottom of
        # the video with LIVE at the right edge. Drag left to scrub back into
        # the provider archive (opens a catch-up segment there). Shown only for
        # timeshift channels; VOD keeps the ordinary seek bar.
        self._ts_depth = 1
        # The same panel as the DVR row, which takes its place by turns on a
        # channel that has both. Its rule names the panel only: a bare
        # "background" here also painted a box behind the slider.
        self.ts_timeline = QWidget(self, objectName="TsTimeline")
        self.ts_timeline.setStyleSheet(
            "QWidget#TsTimeline { background: rgba(16,16,20,215);"
            " border-radius: 8px; }")
        _tl = QHBoxLayout(self.ts_timeline)
        _tl.setContentsMargins(8, 5, 8, 5)
        _tl.setSpacing(8)
        self._ts_switch = _BarSwitch("timeshift")
        self._ts_switch.picked.connect(self._pick_bar)
        _tl.addWidget(self._ts_switch)
        self.ts_slider = _SeekSlider()
        self.ts_slider.setMouseTracking(True)   # hover tooltips w/o a pressed button
        self.ts_slider.seek_requested.connect(self._on_ts_seek)
        self.ts_label = QLabel("● " + tr("mv_live"))
        self.ts_label.setStyleSheet(
            "background: transparent; color: #FFFFFF;"
            "font-size: 11px; font-weight: 700;")
        # Jump straight back to the live edge from anywhere in the archive.
        self.ts_live_btn = QPushButton("⏭ " + tr("mv_live"))
        self.ts_live_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ts_live_btn.setStyleSheet(
            "QPushButton{background: #FF5C5C; color:#fff; border:none;"
            "border-radius: 8px; padding: 2px 10px; font-size: 11px;"
            "font-weight: 700;}"
            "QPushButton:hover{background:#e14b4b;}")
        self.ts_live_btn.clicked.connect(lambda: self.timeshift_seek.emit(0))
        self.ts_live_btn.hide()
        _tl.addWidget(self.ts_slider, 1)
        _tl.addWidget(self.ts_label)
        _tl.addWidget(self.ts_live_btn)
        self.ts_timeline.hide()

    # -- timeshift live timeline ---------------------------------------------

    @staticmethod
    def _fmt_offset(mins: int) -> str:
        mins = max(0, int(mins))
        d, rem = divmod(mins, 1440)
        h, m = divmod(rem, 60)
        if d:
            return tr("dur_days_hours", d=d, h=h)
        if h:
            return f"{h}:{m:02d}"
        return tr("opt_minutes", n=m)

    TS_BAR_HIDE_MS = 3500

    def _ts_hover_time_label(self, frac: float) -> str | None:
        """Wall-clock time at fraction *frac* of the live timeline (0 = oldest
        archived, 1 = live edge), so hovering the bar shows roughly where a
        click would land. Returns None outside timeline mode."""
        if self._seek_mode != "timeline" or self._ts_depth <= 0:
            return None
        offset_min = self._ts_depth * (1.0 - max(0.0, min(1.0, frac)))
        clock = time.strftime("%H:%M", time.localtime(time.time()
                                                      - offset_min * 60))
        if offset_min < 0.5:
            return f"{clock} · {tr('mv_live')}"
        return f"{clock} · −{self._fmt_offset(offset_min)}"

    def enter_timeshift(self, depth_min: int) -> None:
        """Show the live timeline spanning the last *depth_min* minutes."""
        self._ts_depth = max(1, int(depth_min))
        self.ts_slider.setRange(0, self._ts_depth)
        self.ts_slider.setValue(self._ts_depth)
        self.ts_slider.set_time_provider(self._ts_hover_time_label)
        self.ts_label.setText("● " + tr("mv_live"))
        self.ts_live_btn.hide()
        # With the DVR row to come in its place, no timeline first.
        if not (self._local_buffer_enabled and self._bar_view == "dvr"):
            self._show_ts_timeline()
        # On the very first play (esp. the auto-preview at startup) the video
        # surface hasn't been laid out at its docked size yet, so the geometry
        # read above is stale and the bar lands too high. Reposition once the
        # layout has settled.
        QTimer.singleShot(0, self._reposition_ts_timeline)

    def _show_ts_timeline(self) -> None:
        """Reveal the timeshift timeline and arm its idle auto-hide. Only acts
        in timeline mode, so a stray mouse-move on a VOD/live stream can't
        summon it."""
        if self._seek_mode != "timeline":
            return
        if self._bars_shared():
            if self._bar_view != "timeshift":
                return          # the DVR row has this place
            self._set_local_buffer_visible(False)
        # Same as _show_seek_overlay: only the fade timer is re-armed while
        # it's already on screen (this is called from the mouse-move path).
        if not self.ts_timeline.isVisible():
            self._place_ts_timeline()
            self.ts_timeline.show()
            self.ts_timeline.raise_()
            if self.local_buffer_bar.isVisible():
                self._place_local_buffer()      # above the timeline
        self._ts_hide_timer.start()

    def _hide_ts_timeline(self) -> None:
        # Keep it up while the user is scrubbing it or hovering over it.
        if self.ts_slider.dragging or self.ts_timeline.underMouse():
            self._ts_hide_timer.start()
            return
        self.ts_timeline.hide()

    def _reposition_ts_timeline(self) -> None:
        if self.ts_timeline.isVisible():
            self._place_ts_timeline()

    def exit_timeshift(self) -> None:
        self._ts_hide_timer.stop()
        self.ts_timeline.hide()

    def set_timeline_segments(self, segments) -> None:
        """Programme segments for the live timeline: boundary ticks plus a
        hover tooltip naming the programme at that point. Each segment is a
        (start_frac, end_frac, title, time_label) tuple, oldest to live edge."""
        self.ts_slider.set_segments(segments)

    def update_timeshift_position(self, offset_min: float,
                                  title: str | None = None,
                                  paused: bool = False) -> None:
        """Move the marker to *offset_min* behind live, unless the user is
        dragging it right now. *title* names the programme at that point so the
        user can see where in the schedule they are. *paused* forces the
        'not live' state (label + Go-live button) the instant a live-edge stream
        is paused, without waiting for the offset to grow past the tolerance."""
        # Keep the (possibly auto-hidden) timeline model current in timeline
        # mode, so it's right the instant it's revealed. Don't fight a drag.
        if self._seek_mode != "timeline" or self.ts_slider.dragging:
            return
        val = max(0, self._ts_depth - int(round(offset_min)))
        self.ts_slider.blockSignals(True)
        self.ts_slider.setValue(val)
        self.ts_slider.blockSignals(False)
        # ~5 s tolerance counts as live; sub-minute gaps show in seconds so a
        # short DVR pause reads as "−30s behind" rather than a false "LIVE".
        # A pause is 'not live' immediately, regardless of the tiny offset.
        live = (not paused) and offset_min < (5 / 60.0)
        secs = int(round(offset_min * 60))
        if live:
            edge = "● " + tr("mv_live")
        elif paused and secs <= 0:
            edge = "⏸ " + tr("player_paused")
        elif offset_min < 1:
            edge = f"−{secs}s"
        else:
            edge = f"−{self._fmt_offset(offset_min)}"
        self.ts_label.setText(f"{edge} · {title}" if title else edge)
        self.ts_live_btn.setVisible(not live)

    def _on_ts_seek(self, value: int) -> None:
        self.timeshift_seek.emit(int(self._ts_depth - value))

    def _timeline_step(self, minutes: int) -> None:
        """Nudge the live timeline by *minutes* (negative = back into the
        archive, positive = toward live) - the arrow-key equivalent of dragging
        the slider."""
        if self._seek_mode != "timeline":
            return
        self._bar_view = "timeshift"    # where the step goes
        val = max(0, min(self._ts_depth, self.ts_slider.value() + minutes))
        self.ts_slider.setValue(val)
        self.timeshift_seek.emit(int(self._ts_depth - val))
        self._show_ts_timeline()   # reveal so the user sees where they landed

    def _place_ts_timeline(self) -> None:
        margin = 10
        vg = self._ov_surface.geometry()
        self.ts_timeline.setFixedWidth(max(240, vg.width() - 2 * margin))
        self.ts_timeline.adjustSize()
        if self._fs_ui:
            # Fullscreen: the control bar floats at the bottom, so sit above the
            # zone it reserves (bottom margin 24 + bar height) with a gap, even
            # while the bar is auto-hidden - otherwise the two overlap.
            ctrl_h = self.fs_controls.sizeHint().height()
            y = self.height() - 24 - ctrl_h - 12 - self.ts_timeline.height()
        else:
            y = vg.y() + vg.height() - self.ts_timeline.height() - margin
        self.ts_timeline.move(vg.x() + margin, max(margin, y))

    # -- the DVR row -----------------------------------------------------------

    def local_buffer_window(self) -> BufferWindow | None:
        if not getattr(self, "_local_buffer_enabled", False) or self.current_url is None:
            return None
        if self._dvr is not None:
            return self._dvr_window()
        m = self.video.mpv
        try:
            return buffer_window(m.demuxer_cache_state, m.playback_time)
        except Exception:
            return None

    def _set_local_buffer_visible(self, visible: bool) -> None:
        bar = self.local_buffer_bar
        if visible and self._fs_ui:
            visible = False     # fullscreen shows the DVR on its own scrubber
        if not visible:
            if not bar.isHidden():
                bar.hide()
            return
        # Placed only as it appears: re-placing on every pointer event was a
        # layout pass (and a repaint of the video) each time.
        if bar.isHidden():
            self._place_local_buffer()
            bar.show()
            bar.raise_()

    def _place_local_buffer(self) -> None:
        margin = 10
        bar = self.local_buffer_bar
        vg = self._ov_surface.geometry()
        bar.setFixedWidth(max(220, vg.width() - 2 * margin))
        bar.adjustSize()
        y = vg.y() + vg.height() - bar.height() - margin
        if self.ts_timeline.isVisible():
            # A catch-up channel's timeline sits at the bottom too: go above.
            y = min(y, self.ts_timeline.y() - bar.height() - 6)
        bar.move(vg.x() + margin, max(margin, y))

    def _reveal_local_buffer(self) -> None:
        if not getattr(self, "_local_buffer_ready", False):
            return
        if self._bars_shared():
            if self._bar_view != "dvr":
                return          # the timeshift timeline has this place
            self._ts_hide_timer.stop()
            self.ts_timeline.hide()
        self._set_local_buffer_visible(True)
        self._local_buffer_hide_timer.start()

    def _bars_shared(self) -> bool:
        """A catch-up channel watched live with a rewind buffer: the DVR row
        and the timeshift timeline take turns in one place. Not in
        fullscreen, where the DVR row never shows."""
        return (self._seek_mode == "timeline" and not self._fs_ui
                and bool(getattr(self, "_local_buffer_enabled", False))
                and bool(getattr(self, "_local_buffer_ready", False)))

    def _sync_bar_switch(self) -> bool:
        """The switch shows on both bars while they take turns."""
        shared = self._bars_shared()
        if shared == self._dvr_switch.isHidden():
            self._dvr_switch.setVisible(shared)
            self._ts_switch.setVisible(shared)
        return shared

    def _chosen_bar(self) -> str:
        s = self._settings
        if s is not None and s.value("rewind_bar_view", "dvr") == "timeshift":
            return "timeshift"
        return "dvr"

    def _pick_bar(self, view: str) -> None:
        """The switch on either bar: show the other one in its place, and
        keep the choice for the next catch-up channel."""
        if view not in ("dvr", "timeshift"):
            return
        self._bar_view = view
        if self._settings is not None:
            self._settings.setValue("rewind_bar_view", view)
        if view == "dvr":
            self._reveal_local_buffer()
        else:
            self._show_ts_timeline()

    def _hide_local_buffer(self) -> None:
        if (self.local_buffer_seek.dragging or self.local_buffer_bar.underMouse()
                or QApplication.activePopupWidget() is not None):
            self._local_buffer_hide_timer.start()
            return
        self._set_local_buffer_visible(False)

    def _update_local_buffer(self, position: float | None) -> BufferWindow | None:
        window = self.local_buffer_window()
        self._local_buffer_ready = window is not None
        shared = self._sync_bar_switch()
        self._local_behind = None
        if window is None:
            self._local_buffer_hide_timer.stop()
            self._set_local_buffer_visible(False)
            self._local_slider_window = None
            self._local_fs_window = None
            return None
        pos = position or 0.0
        if getattr(self, "_paused", False):
            self._dvr_live = False
        if self._dvr_live:
            self._live_ahead = max(0.0, window.end - pos)
        live_end = max(window.start, window.end - self._live_ahead)
        self._dvr_span = live_end - window.start
        behind = max(0.0, live_end - pos)
        lag = self._live_lag.observe(time.monotonic(), window.end)
        if lag >= LIVE_REOPEN_LAG:
            behind += lag   # the time to live, not just to the buffer's end
        at_live = self._dvr_live and behind < 1
        self._local_behind = 0.0 if at_live else behind
        self._label_live_button()
        if self.local_buffer_live.property("atLive") != at_live:
            self.local_buffer_live.setProperty("atLive", at_live)
            st = self.local_buffer_live.style()
            st.unpolish(self.local_buffer_live)
            st.polish(self.local_buffer_live)
        span = _format_time(self._dvr_span)
        # With the switch beside it, its lit DVR names the row already.
        name = "" if shared else "DVR "
        self.local_buffer_time.setText(
            f"{name}{span}" if at_live
            else f"{name}−{_format_time(behind)} / {span}")
        if not self.local_buffer_seek.dragging:
            self._local_slider_window = window
            self.local_buffer_seek.setRange(0, max(1, int(self._dvr_span)))
            self.local_buffer_seek.setValue(
                int(min(self._dvr_span, max(0.0, pos - window.start))))
        return window

    def local_behind(self) -> float | None:
        """Seconds behind live in the rewind buffer; None without one."""
        return getattr(self, "_local_behind", None)

    def _seek_local_target(self, target: float, window: BufferWindow) -> None:
        # Up to the live point is following live again; anything before it
        # is rewound.
        self._dvr_live = target >= window.end - self._live_ahead - 1.0
        if self._dvr is not None:
            self._dvr_seek(target, window)
            return
        m = self.video.mpv
        if m is not None:
            try:
                m.command("seek", window.clamp(target), "absolute+exact")
            except Exception as e:
                log.debug("local buffer seek failed: %s", e)

    def _seek_local_buffer(self, seconds: int,
                           origin: BufferWindow | None = None) -> None:
        origin = origin or self._local_slider_window
        # Re-read on release: a long drag can outlive the cache range it began on.
        window = self.local_buffer_window()
        if window is not None and origin is not None:
            self._seek_local_target(origin.start + seconds, window)

    def _label_live_button(self, force: bool = False) -> None:
        """LIVE says which it does: go to the end of the buffer, or - far
        behind the channel - open the channel again, which starts the
        rewind buffer over. Changed only when that flips."""
        reopen = (self._live_lag.lag >= LIVE_REOPEN_LAG
                  and bool(self.receivers(self.reopen_live)))
        if not force and reopen == getattr(self, "_live_reopens", None):
            return
        self._live_reopens = reopen
        self.local_buffer_live.setText(
            ("↻ " if reopen else "") + tr("mv_live"))
        self.local_buffer_live.setToolTip(
            tr("dvr_live_reopen_tip") if reopen else tr("dvr_live_tip"))

    def _local_go_live(self) -> None:
        window = self.local_buffer_window()
        if window is None:
            return
        # Paused or rewound past the network buffer, mpv stopped reading, so
        # the end of the buffer is that far behind the channel: only opening
        # it again reaches live. That starts a new buffer.
        if (self._live_lag.lag >= LIVE_REOPEN_LAG
                and self.receivers(self.reopen_live)):
            log.info("DVR: %.0f s behind live, opening the channel again",
                     self._live_lag.lag)
            self.reopen_live.emit()
            return
        self.go_to_buffer_end(window)

    def go_to_buffer_end(self, window: BufferWindow | None = None) -> None:
        window = window or self.local_buffer_window()
        if window is not None:
            # Back to the live point, the forward buffer kept: the very end of
            # what arrived would leave nothing to play from and stall.
            self._seek_local_target(window.end - self._live_ahead, window)
            self._dvr_live = True
            self._user_paused = False
            self._set_mpv("pause", False)
