"""The pop-out's picture: what plays, shown in another window.

The real video widget never moves between windows. A surface on its own
thread draws into the pop-out's own native window; on Linux and Windows
(and on macOS with DOPEIPTV_MAC_RASTER=1) the docked surface renders into
offscreen buffers the pop-out shows as images - the raster mirror, paced
by a 60 Hz tick and mpv's new-frame flag; else (macOS) a second GL surface
shares the docked one's render context. Meanwhile a placeholder covers the
docked video, the overlays float over the mirror instead, and the pop-out's
control bar may hide itself until the pointer moves. The detached-window
mode (set_popout_mode) and the settling after a reparent are here too.

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

import os
import sys
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtOpenGL import QOpenGLFramebufferObject
from PyQt6.QtWidgets import QPushButton, QWidget

from ..core.log import log
from ..i18n import tr
from .video_surface import _MpvGLWidget, _RasterMirror


class PopoutMirrorMixin:
    # -- detached pop-out window -----------------------------------------------

    # -- macOS mirror pop-out (no reparent) ----------------------------------

    def start_mirror(self, parent: QWidget) -> "_MpvGLWidget":
        """macOS pop-out: build a MIRROR GL surface in *parent* (the pop-out
        window) that renders this player's live mpv stream. The real video
        widget is never moved - reparenting a QOpenGLWidget between windows on
        macOS leaves cocoa presenting a stale (frozen) layer. Instead the mpv
        render context is drawn into a second surface that shares GL resources,
        and the docked video (still rendering, behind a placeholder) keeps
        driving mpv's frame timing. Returns the mirror widget for the caller to
        place in the window."""
        _t_start = time.monotonic()
        # macOS can opt into the raster path with DOPEIPTV_MAC_RASTER=1.
        #
        # It is the same symptom here as there: a GL surface in a SECOND
        # top-level window keeps presenting the frame it had, while
        # rendering reports success the whole way. Linux and Windows both
        # left the GL mirror behind for exactly that, and the raster path
        # has been solid on both since. macOS kept the GL mirror because it
        # was "proven there" - and the fullscreen freeze says it no longer
        # is. This is opt-in rather than the default so the two can be
        # compared in ONE build: same binary, one environment variable.
        #
        # It also brings the stall watchdog, which lives in the raster
        # tick and has therefore never run on macOS.
        _mac_raster = (sys.platform == "darwin"
                       and os.environ.get("DOPEIPTV_MAC_RASTER") == "1")
        threaded = getattr(self.video, "threaded", False) is True
        if threaded:
            # The picture's own thread draws into the pop-out's own native
            # window instead (media/video_thread.py): nothing is read back,
            # nothing GL moves between windows, no timer drives it.
            m = self.video.make_mirror(parent)
            tick, interval = None, 0
        elif (sys.platform.startswith("linux") or sys.platform == "win32"
                or _mac_raster):
            # DEFAULT Linux and Windows path: raster mirror (see
            # _RasterMirror). Every GL presentation route in a second window
            # shows black on modern Qt/Mesa (X11 and Wayland alike), so all GL
            # stays in the docked widget's proven context and the pop-out gets
            # plain images.
            #
            # Windows joined this path after the GL mirror there rendered the
            # picture upside down and then black - the same symptoms Linux had,
            # and the same cause: a second top-level window's GL surface. macOS
            # keeps the GL mirror, which is proven there.
            # Pacing: a PRECISE 60 Hz poll whose tick renders ONLY when mpv
            # reports a new frame pending - so readbacks run at the stream's
            # real fps with <=16 ms cadence error. (A 30 fps coarse poll beat
            # against 25 fps streams - judder; driving ticks from mpv's update
            # callback flooded the main thread with readbacks - far worse.)
            m = _RasterMirror(parent, mirror_of=self.video)
            self._raster_state = ""
            # New-frame gate: mpv's update callback sets a cheap flag (the
            # slot must NOT render - the callback fires more often than the
            # video fps, and rendering from it flooded the main thread); the
            # 60 Hz tick consumes it. This replaces polling
            # mpv_render_context_update, which silently gated nothing when
            # the binding lacks update() (the try/except defaulted to
            # "always render" = 60 readbacks/s for a 25 fps stream).
            self._raster_pending_frame = True
            self.video.frame_ready.connect(self._raster_mark_frame)
            tick, interval = self._tick_raster_mirror, 16
        else:
            m = _MpvGLWidget(parent, mirror_of=self.video)
            m.setMouseTracking(True)
            tick, interval = m.update, 16
        self._mirror = m
        # Raster mirror: double-buffered offscreen targets. The tick reads
        # LAST tick's buffer (whose GPU work finished long ago - no stall)
        # and submits this tick's render into the other one.
        self._mirror_fbos = [None, None]
        self._mirror_fbo_i = 0
        self._mirror_pending = None
        # Drive the mirror with a steady poll timer, NOT mpv's frame callback:
        # a timer can never stall on a window resize/maximize (the freeze
        # reported when the pop-out was maximized), and while popped out the
        # mirror is the only render loop, so it also paces mpv. Suspend the
        # docked surface so the GPU renders the stream once, not twice.
        self.video._render_suspended = True
        # One forced repaint so the suspended surface CLEARS TO BLACK now.
        # Without it the frame-driven repaints stop while the last video frame
        # is still on the surface, and any gap around the placeholder shows it
        # frozen - intermittently, depending on how the reflow interleaved.
        self.video.update()
        if tick is not None:
            self._mirror_timer = QTimer(self)
            # Precise, not coarse: Qt's default timer slack (~5%) is visible
            # as frame-pacing jitter at video rates.
            self._mirror_timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._mirror_timer.timeout.connect(tick)
            self._mirror_timer.start(interval)
        # Float the seek bar, timeshift timeline and stats over the mirror:
        # reparent them into the pop-out window and anchor placement to the
        # mirror. They're plain widgets, so moving them is safe.
        self._ov_surface = m
        m.installEventFilter(self)      # its resizes re-place the bars
        for ov in (self.seek_overlay, self.ts_timeline, self._stats_overlay,
                   self.nextup_btn, self.local_buffer_bar, self.stream_info_badge,
                   self._ld_overlay):
            if ov is not None:
                ov.setParent(parent)
                ov.hide()
        # The sleep-timer badge follows into the pop-out too; flash it there if
        # a timer is armed (then it fades / stays pinned per the countdown).
        self.sleep_badge.setParent(parent)
        self.sleep_badge.hide()
        self.number_badge.setParent(parent)
        self.number_badge.hide()
        self._show_dock_placeholder(True)
        # Re-place once the reflow has settled: the caller still has to move
        # the control bar out and re-lock the height after this returns, and a
        # placeholder sized against the pre-reflow rect left a strip uncovered
        # (the intermittent "video over the control bar").
        QTimer.singleShot(0, lambda: self._show_dock_placeholder(True))
        self._reveal_sleep_badge()
        # Stats that were on come along: now, and once more against the
        # settled window (the caller adds the mirror and the bar after this).
        self._keep_stats_up()
        QTimer.singleShot(0, self._keep_stats_up)
        # A stream still on its way says so over the mirror too.
        self._ld_refresh()
        QTimer.singleShot(0, self._ld_refresh)
        # Every fullscreen toggle tears this down and builds it again - a new
        # GL context and a fresh mpv render binding each time. Timed so a
        # stall at the transition can be attributed to the rebuild rather
        # than guessed at.
        log.info("VID mirror start took %.0fms (path=%s)",
                 (time.monotonic() - _t_start) * 1000,
                 "thread" if threaded else
                 "raster" if isinstance(m, _RasterMirror) else "gl")
        return m

    def _raster_mark_frame(self) -> None:
        """mpv produced a new frame: set the flag the 60 Hz tick consumes.
        This slot must stay this cheap - rendering directly from the update
        callback flooded the main thread (it fires more often than the video
        fps)."""
        self._raster_pending_frame = True

    def _tick_raster_mirror(self) -> None:
        """One raster-mirror frame: render mpv into an offscreen FBO in the
        DOCKED widget's GL context (the one that provably presents) and hand
        the readback to the pop-out's raster widget. The docked mpv/render
        context are never moved or freed."""
        m = self._mirror
        v = self.video
        if m is None or v.mpv is None or v._ctx is None:
            return
        try:
            ratio = float(m.devicePixelRatioF() or 1.0)
            w = max(2, int(m.width() * ratio))
            h = max(2, int(m.height() * ratio))
            # Cap the offscreen render (and so the readback) at 1080p: mpv
            # downscales on the GPU for free, while an uncapped maximized
            # pop-out meant a ~14 MB synchronous glReadPixels per frame - the
            # "lags deluxe when maximized". The raster widget scales the frame
            # up smoothly; for TV content the difference is invisible.
            scale = min(1.0, 1920 / w, 1080 / h)
            w = max(2, int(w * scale))
            h = max(2, int(h * scale))
            # A resize drops the whole pipeline (both buffers + the pending
            # frame) so no stale-size frame is ever shown.
            fbos = self._mirror_fbos
            if fbos[0] is not None and (fbos[0].width() != w
                                        or fbos[0].height() != h):
                self._mirror_pending = None
                self._mirror_fbos = fbos = [None, None]
                self._mirror_fbo_i = 0
            # Render only when mpv actually produced a new frame (live TV is
            # 25 fps against the 60 Hz tick; the frame_ready flag is set by
            # mpv's update callback) - but always collect a finished pending
            # frame.
            upd = getattr(self, "_raster_pending_frame", True)
            # Watchdog: the gate only opens when mpv's update callback fires.
            # If that signal is lost across a window transition - which is
            # what a fullscreen switch is - the mirror keeps ticking but
            # never renders again: the picture freezes on its last frame
            # while the audio plays on, until some window event forces a
            # repaint (the "tab out and back and it works" report). Force a
            # frame through after a quarter second of silence so it can heal
            # itself instead of waiting for the user.
            now_m = time.monotonic()
            if upd:
                self._raster_frame_at = now_m
            elif now_m - getattr(self, "_raster_frame_at", now_m) > 0.25:
                self._raster_frame_at = now_m
                self._raster_stalls = getattr(self, "_raster_stalls", 0) + 1
                if self._raster_stalls in (1, 10, 100):
                    log.info("VID raster mirror: no new frame for 250 ms - "
                             "forcing one (stall #%d)", self._raster_stalls)
                upd = True
            pend = self._mirror_pending
            # Heartbeat: this path only logged on a state CHANGE, so a mirror
            # that ticks but never produces a frame looked identical to one
            # that had stopped ticking altogether - which is exactly the
            # question a frozen pop-out asks. Report the gate itself: how often
            # we tick, how many of those ticks mpv had a new frame for, and how
            # many images actually reached the widget.
            self._raster_n = getattr(self, "_raster_n", 0) + 1
            if upd:
                self._raster_upd_n = getattr(self, "_raster_upd_n", 0) + 1
            if self._raster_n % 120 == 0:
                now_t = time.monotonic()
                since_t = now_t - getattr(self, "_raster_hb_at", now_t)
                self._raster_hb_at = now_t
                log.debug("VID raster tick #%d %.1f/s new-frame=%d shown=%d "
                          "size=%dx%d pending=%s",
                          self._raster_n,
                          (120 / since_t if since_t > 0 else 0.0),
                          getattr(self, "_raster_upd_n", 0),
                          getattr(self, "_raster_shown_n", 0),
                          w, h, pend is not None)
            if not upd and pend is None:
                return
            img = None
            v.makeCurrent()
            try:
                # 1) Read LAST tick's frame first: its GPU work finished
                #    during the ~40 ms since, so glReadPixels returns without
                #    stalling. (Reading AFTER submitting this tick's render
                #    would wait for that too - GL is in-order.) flipped=False
                #    skips toImage's mirror pass - which is why the render
                #    below drops mpv's flip_y too: upright needs the two
                #    toggles to MATCH (flip_y=False+flipped=True was inverted
                #    on hardware; True+True was upright).
                if pend is not None:
                    img = pend.toImage(False)
                    self._mirror_pending = None
                # 2) Submit this tick's render into the OTHER buffer; it is
                #    read on the next tick.
                if upd:
                    self._raster_pending_frame = False
                    i = self._mirror_fbo_i
                    if fbos[i] is None:
                        fbos[i] = QOpenGLFramebufferObject(w, h)
                    # block_for_target_time=False is THE fix for the pop-out
                    # lag: mpv_render_context_render() defaults to BLOCKING
                    # until the frame's target display time, so every tick
                    # stalled the GUI thread for most of a frame interval -
                    # independent of the pop-out's size (which is why capping
                    # the resolution never helped), and worst on 23.976 fps
                    # film against a 60 Hz tick. We present via a readback,
                    # not a vsync'd swap, so mpv must never pace us.
                    v._ctx.render(flip_y=False, block_for_target_time=False,
                                  opengl_fbo={
                                      "fbo": int(fbos[i].handle()),
                                      "w": w, "h": h})
                    # Keep reporting the present: dropping it degraded mpv's
                    # timing estimate and made the judder worse.
                    try:
                        v._ctx.report_swap()
                    except Exception:
                        pass
                    self._mirror_pending = fbos[i]
                    self._mirror_fbo_i = 1 - i
            finally:
                v.doneCurrent()
            if img is not None:
                self._raster_shown_n = getattr(self, "_raster_shown_n", 0) + 1
                m.set_image(img)   # painted stretched to the widget rect
            if getattr(self, "_raster_state", "") != "render":
                self._raster_state = "render"
                log.info("VID raster-mirror -> render (%dx%d dpr=%.1f)",
                         w, h, ratio)
        except Exception as e:
            if getattr(self, "_raster_state", "") != "fail":
                self._raster_state = "fail"
                log.info("VID raster-mirror -> FAIL: %s", e)

    def stop_mirror(self) -> None:
        """Tear the mirror down and hand the picture back to the docked
        surface. Safe to call when no mirror is active."""
        _t_stop = time.monotonic()
        t = getattr(self, "_mirror_timer", None)
        if t is not None:
            t.stop()
            self._mirror_timer = None
        # Raster mirror: stop feeding the new-frame flag.
        try:
            self.video.frame_ready.disconnect(self._raster_mark_frame)
        except (TypeError, RuntimeError):
            pass
        # Raster mirror: release the offscreen FBOs while the docked GL context
        # (their owner) is current. The render context itself was never moved.
        if any(getattr(self, "_mirror_fbos", [None, None])):
            try:
                self.video.makeCurrent()
                self._mirror_fbos = [None, None]
                self.video.doneCurrent()
            except Exception:
                self._mirror_fbos = [None, None]
        self._mirror_pending = None
        self.video._render_suspended = False
        drop = getattr(self.video, "drop_mirror", None)
        if drop is not None:
            drop()                # the picture's own thread: back to docked
        # Overlays back onto the docked player.
        self._ov_surface = self.video
        for ov in (self.seek_overlay, self.ts_timeline, self._stats_overlay,
                   self.nextup_btn, self.local_buffer_bar, self.stream_info_badge,
                   self._ld_overlay):
            if ov is not None:
                ov.setParent(self)
                ov.hide()
        self.sleep_badge.setParent(self)
        self.sleep_badge.hide()
        self.number_badge.setParent(self)
        self.number_badge.hide()
        self._mirror = None
        self._show_dock_placeholder(False)
        # The control bar is back in the player by now (the caller re-adds it
        # before stop_mirror) - re-pin the docked height WITH the bar again.
        self._lock_video_box()
        self._relayout_controls()
        self.video.update()
        self._reveal_sleep_badge()
        self._keep_stats_up()
        QTimer.singleShot(0, self._keep_stats_up)
        self._ld_refresh()
        QTimer.singleShot(0, self._ld_refresh)
        log.info("VID mirror stop took %.0fms",
                 (time.monotonic() - _t_stop) * 1000)

    def reveal_pop_overlays(self) -> None:
        """macOS/Windows pop-out hover: bring back the auto-hidden control bar
        and flash the seek bar (VOD / catch-up) or the timeshift timeline,
        whichever applies. The mirror forwards its pointer movement here; the
        overlays are already reparented over it."""
        self._reveal_local_buffer()
        self._reveal_popout_bar()       # no-op unless auto-hide is on
        self._reveal_sleep_badge()      # no-op unless a sleep timer is armed
        if self._seek_mode == "timeline":
            self._show_ts_timeline()
        else:
            self._show_seek_overlay()   # a no-op on plain live

    def _show_dock_placeholder(self, on: bool) -> None:
        """Cover (not hide) the docked video with a 'playing in pop-out' panel
        while mirrored, so the detail pane doesn't show the same picture twice.
        Covering rather than hiding keeps the docked surface rendering, which
        is what keeps mpv's timing and the shared render context healthy."""
        ph = getattr(self, "_dock_ph", None)
        if on:
            if ph is None:
                ph = self._dock_ph = QPushButton(tr("popout_placeholder"), self)
                ph.setCursor(Qt.CursorShape.PointingHandCursor)
                ph.setStyleSheet(
                    "QPushButton { background:#000000; color:#B8B8C0;"
                    " border:none; font-size:13px; font-weight:600; }"
                    "QPushButton:hover { color:#FFFFFF; }")
                ph.clicked.connect(lambda: self.popout_requested.emit())
            # Cover the WHOLE player, not just the video rect: while mirrored
            # the control bar has moved to the pop-out, so the player shrinks
            # and the video stretches into the freed strip. Sizing to a rect
            # measured before that reflow left the (suspended, so no longer
            # repainting) GL surface visible there as a frozen frame - "video
            # rendered over the control bar".
            ph.setGeometry(self.rect())
            ph.show()
            ph.raise_()
        elif ph is not None:
            ph.hide()

    def set_popout_mode(self, enabled: bool) -> None:
        """Detached-window mode: the pop-out window drives the size, so release
        the docked fixed height and let the video fill the window."""
        log.info("VID set_popout_mode(%s): video=%dx%d ctx=%s mpv=%s",
                 enabled, self.video.width(), self.video.height(),
                 self.video._ctx is not None, self.video.mpv is not None)
        self._popout_mode = enabled
        if not enabled:
            # Back to docked: the bar is always visible there.
            self._popout_autohide = False
            self._popout_bar_timer.stop()
        self.bar.show()
        self.pop_btn.setToolTip(
            tr("tooltip_popout_exit") if enabled else tr("tooltip_popout"))
        self.pop_btn.setAccessibleName(self.pop_btn.toolTip())
        self._lock_video_box()
        # Re-settle once the layout is final. A pop-out toggle reparents the
        # player, so right after it the video geometry isn't settled: the fixed
        # video box measured a not-yet-laid-out control bar (seek bar sat over
        # the toolbar on dock-back), and the timeshift timeline / seek bar,
        # placed against the stale geometry, sat in the middle of the video on
        # pop-out until a stray event nudged them. The deferred pass re-measures
        # and re-places against the settled geometry - both directions.
        QTimer.singleShot(0, self._settle_after_reparent)

    def _settle_after_reparent(self) -> None:
        """Re-lock the video box and re-place any visible overlay against the
        geometry once it has settled after a pop-out dock/undock reparent."""
        self._lock_video_box()
        if self.ts_timeline.isVisible():
            self._place_ts_timeline()
        if (self.seek_overlay is not None
                and self.seek_overlay.isVisible()):
            self._place_seek_overlay()
        if self.local_buffer_bar.isVisible():
            self._place_local_buffer()
        # macOS keeps the GL context across the reparent, so the widget's
        # backing framebuffer can stay bound to the old window and show a
        # frozen frame. A 1 px resize and back rebuilds it. Pure widget
        # geometry - mpv and the render context are never touched.
        v = self.video
        sz = v.size()
        if sz.isValid() and sz.height() > 1:
            v.resize(sz.width(), sz.height() - 1)
            v.resize(sz)
        v.update()
        # The reparent leaves a STALE snapshot of the whole player composition
        # on the new window (proven by "double stats for nerds" in the pop-out:
        # the stale copy plus the live overlay, and the picture frozen even
        # with no overlay at all). An async update()/resize can lose the race
        # with that stale layer. Force the host window to rebuild its entire
        # backing store SYNCHRONOUSLY, now - the one presentation-level lever
        # not yet tried. macOS only; mpv is untouched.
        if sys.platform == "darwin":
            win = self.window()
            if win is not None:
                win.repaint()
                log.info("VID host-window repaint (rebuild stale backing store)")

    def _in_popout(self) -> bool:
        """True whenever the control bar lives in a pop-out window - either the
        reparent path (Linux, `_popout_mode`) or the mirror path (macOS/Windows,
        a live `_mirror`). Docked, the bar is always visible."""
        return self._popout_mode or getattr(self, "_mirror", None) is not None

    def set_popout_autohide(self, enabled: bool) -> None:
        """When on, the pop-out control bar fades after a few seconds of no
        pointer activity and reappears on mouse movement over the video (like a
        video player). Off keeps it permanently visible."""
        self._popout_autohide = enabled
        self.bar.show()
        if enabled and self._in_popout():
            self._popout_bar_timer.start()
        else:
            self._popout_bar_timer.stop()

    def _hide_popout_bar(self) -> None:
        if self._in_popout() and self._popout_autohide and not self._fs_ui:
            if self.bar.underMouse() or self.local_buffer_seek.dragging:
                self._popout_bar_timer.start()
                return
            self.bar.hide()

    def _reveal_popout_bar(self) -> None:
        if self._in_popout() and self._popout_autohide:
            self.bar.show()
            self._popout_bar_timer.start()
