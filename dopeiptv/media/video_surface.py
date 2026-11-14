"""The video surface: the widget mpv draws into (libmpv's OpenGL render
API) and the plain raster mirror the Linux pop-out shows of it."""

from __future__ import annotations

import os
import sys
import time

from PyQt6.QtCore import QByteArray, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QOpenGLContext, QPainter
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..core.log import log
from ..i18n import tr
from .players import _libmpv, _register_error_callback


def _env_num(name: str, default, cast):
    """Read a numeric override from the environment, falling back to *default*
    when unset or malformed (so a stray value can never abort player start)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


class _MpvGLWidget(QOpenGLWidget):
    """Video surface that owns the mpv render context."""

    frame_ready = pyqtSignal()
    playback_error = pyqtSignal(str)
    # mpv's end-file event said the file ended normally. The authoritative
    # end-of-playback signal; the polled eof-reached property is the backup.
    eof_event = pyqtSignal()
    video_mouse_press = pyqtSignal(object)
    video_mouse_move = pyqtSignal(object)
    video_mouse_release = pyqtSignal(object)
    video_dbl_click = pyqtSignal()
    video_key_press = pyqtSignal(object)
    video_key_release = pyqtSignal(object)

    EXTRA_OPTS: dict = {}

    def __init__(self, parent: QWidget | None = None,
                 mirror_of: "_MpvGLWidget | None" = None) -> None:
        super().__init__(parent)
        if sys.platform == "darwin":
            from ..core.platform_macos import apply_widget_surface_format
            apply_widget_surface_format(self)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        # Focusable so it can receive Left/Right for seeking once clicked.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.mpv = None
        self._ctx = None
        # A MIRROR surface (macOS pop-out) owns no mpv and no render context:
        # it renders the OWNER widget's render context into its own FBO, so the
        # same stream shows in a second window without ever reparenting (and
        # freezing) the real GL surface. mpv only allows one render context per
        # stream, and it renders in any context that shares GL resources with
        # the one it was created against (AA_ShareOpenGLContexts guarantees
        # that here).
        self._mirror_of = mirror_of
        # Set on the DOCKED surface while a macOS mirror pop-out is showing the
        # stream: the docked surface then skips mpv rendering (the mirror is the
        # sole renderer) so the GPU isn't doing the work twice - what made the
        # pop-out laggy and drove mpv's frame-drop count up.
        self._render_suspended = False
        self._gl_init_error: str | None = None
        self._blank = False
        self.frame_ready.connect(self._on_frame_ready)

    def _on_frame_ready(self) -> None:
        # While a mirror presents the stream, do NOT repaint the covered
        # docked surface per frame: every QOpenGLWidget update recomposites
        # the WHOLE main window (25x/s on Wayland) even though this paint
        # only clears to black - the pop-out lag that didn't depend on the
        # pop-out's size. The mirror tick is the render loop then.
        if not self._render_suspended:
            self.update()

    def set_blank(self, blank: bool) -> None:
        """When blanked, paint solid black instead of mpv's last frame. Used on
        stop so the pane clears instead of freezing on the final image."""
        self._blank = blank
        self.update()

    _SEEK_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right)
    _PLAYER_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right,
                    Qt.Key.Key_Up, Qt.Key.Key_Down)

    def keyPressEvent(self, event) -> None:
        # Plain (unmodified) Left/Right seek and Up/Down volume when the video
        # has focus (mpv-style). Ctrl+Left/Right stays a channel zap, so only
        # claim the unmodified presses.
        if (event.key() in self._PLAYER_KEYS
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.video_key_press.emit(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if (event.key() in self._SEEK_KEYS
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.video_key_release.emit(event)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.video_mouse_press.emit(event)

    def mouseMoveEvent(self, event) -> None:
        self.video_mouse_move.emit(event)

    def mouseReleaseEvent(self, event) -> None:
        self.video_mouse_release.emit(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.video_dbl_click.emit()

    def _get_proc_address(self, _, name: bytes) -> int:
        glctx = QOpenGLContext.currentContext()
        if glctx is not None:
            addr = glctx.getProcAddress(QByteArray(name))
            if addr:
                v = int(addr)
                if v:
                    return v
        if sys.platform == "darwin":
            from ..core.platform_macos import gl_get_proc_address_fallback
            return gl_get_proc_address_fallback(name)
        return 0

    @staticmethod
    def _ctx_id(glctx) -> str:
        """Stable identity of a QOpenGLContext for the video trace ("VID"
        log lines): id() of the PyQt wrapper changes between calls even for
        the same underlying context, the sip pointer does not. This is what
        lets a log show whether a reparent kept or replaced the GL context."""
        if glctx is None:
            return "none"
        try:
            from PyQt6 import sip
            return hex(sip.unwrapinstance(glctx))
        except Exception:
            return f"py-{id(glctx):#x}"

    def initializeGL(self) -> None:
        self.makeCurrent()
        glctx = QOpenGLContext.currentContext()
        if self._mirror_of is not None:
            # Mirror surface: nothing to build up front. On macOS/Windows it
            # renders the owner's render context (see paintGL); on Wayland it
            # lazily creates its OWN render context in paintGL (cross-context
            # GL is unreliable there). Hook the free for the own-ctx case so a
            # destroyed pop-out window can never leak a live render context -
            # a no-op when _ctx is None (the macOS/Windows mirror).
            if glctx is not None:
                try:
                    glctx.aboutToBeDestroyed.connect(
                        self._free_render_context,
                        Qt.ConnectionType.UniqueConnection)
                except (TypeError, RuntimeError):
                    pass
            log.info("VID mirror initializeGL glctx=%s size=%dx%d",
                     self._ctx_id(glctx), self.width(), self.height())
            return
        log.info("VID initializeGL glctx=%s re_entry=%s size=%dx%d",
                 self._ctx_id(glctx), self.mpv is not None,
                 self.width(), self.height())
        if glctx is not None:
            v = glctx.format().version()
            log.info("GL context: %s.%s profile=%s", v[0], v[1],
                     glctx.format().profile().name)
            # Free the render context the moment THIS GL context dies. Qt
            # recreates the context whenever the widget is reparented (docking
            # in / out of the pop-out window, moving to another monitor); the
            # MpvRenderContext is bound to the context it was made against, so
            # reusing it across a recreation left the video frozen while audio
            # kept playing. Freeing it here (the old context is still current
            # when this fires) and rebuilding below keeps the video alive.
            # UniqueConnection so re-entry can't stack duplicate handlers; any
            # error must not escape initializeGL (that aborts the process).
            try:
                glctx.aboutToBeDestroyed.connect(
                    self._free_render_context,
                    Qt.ConnectionType.UniqueConnection)
            except (TypeError, RuntimeError):
                pass
        else:
            log.warning("no GL context in initializeGL")
        # A *repeat* initializeGL means the GL context was recreated. Keep the
        # mpv instance untouched - a fresh instance would tear down the playing
        # stream ("Stream error: loading failed" on a window move) - and
        # ALWAYS rebuild the render context against the new context, so the
        # video re-binds without ever interrupting playback or audio.
        # aboutToBeDestroyed normally freed the old context cleanly (while it
        # was still current); free best-effort here too so that if it didn't
        # fire, a stale render context can't wedge the video black.
        if self.mpv is not None:
            self._free_render_context("initializeGL re-entry")
            try:
                self._create_render_context()
                log.info("VID render context rebuilt after GL recreation "
                         "glctx=%s", self._ctx_id(glctx))
            except Exception as e:
                self._ctx = None
                log.error("VID render context rebuild FAILED: %s", e)
            return
        # Building the mpv render context can fail hard on a weak or
        # software-only GL stack (typically a VM with no GPU acceleration).
        # This runs inside a Qt virtual override (initializeGL), and if the
        # exception were allowed to propagate out of it PyQt aborts the whole
        # process with "Fatal Python error: Aborted" - the app never opens.
        # Catch it: leave the video surface blank, keep the app alive, and
        # surface a clear reason instead of a crash. The real machines this
        # targets (with a GPU) never hit this path.
        try:
            self._build_mpv_render()
            log.info("Render context ready")
        except Exception as e:
            self._ctx = None
            self.mpv = None
            self._gl_init_error = f"{type(e).__name__}: {e}"
            log.error("embedded GL init failed, in-app video disabled: %s",
                      self._gl_init_error)
            # Defer the toast so it fires after the window is up, not from
            # inside the GL callback.
            QTimer.singleShot(1500, lambda: self.playback_error.emit(
                tr("embedded_gl_failed")))

    def _build_mpv_render(self) -> None:
        # libmpv hard-requires LC_NUMERIC="C" and segfaults inside
        # mpv_create() on anything else ("Non-C locale detected"). Qt's
        # QApplication constructor calls setlocale(LC_ALL, "") on Unix, so on
        # any system whose locale uses a decimal comma (de_DE, fr_FR, ...)
        # the process arrives here with the wrong numeric locale - the env
        # vars set at startup don't help when the user's LC_ALL overrides
        # them, and native file dialogs can re-run setlocale long after
        # startup. Resetting the one category libmpv cares about, right
        # before the instance is created, is the fix python-mpv's own README
        # prescribes; Qt and Python formatting don't read the C locale, so
        # nothing else changes.
        import locale
        try:
            locale.setlocale(locale.LC_NUMERIC, "C")
        except locale.Error as e:
            log.warning("could not set LC_NUMERIC=C: %s", e)
        # vo=libmpv is mandatory for the render API and exists in every
        # libmpv build - create the instance with just that (plus a quiet
        # terminal), then apply the rest tolerantly below.
        log.info("Creating mpv instance...")
        self.mpv = _libmpv.MPV(vo="libmpv", terminal=False)
        # Best-effort options. Some minimal libmpv builds (notably the one
        # compiled inside our Flatpak) don't implement every option - e.g.
        # 'osc'. Passing them all to the constructor makes ONE unknown
        # option abort the whole player ("mpv option does not exist"), so
        # set them one at a time and skip any this build rejects.
        soft = {"user_agent": "dopeIPTV/1.0", "keep_open": "yes",
                "input_default_bindings": False, "input_vo_keyboard": False,
                # Video decoding. Default is 'no' (software) - the same default
                # mpv itself ships. Modern CPUs decode even 4K 10-bit HEVC/HDR
                # comfortably, and software decode is completely immune to the
                # GPU/driver hazards of the libmpv OpenGL render API (e.g. the
                # nvidia-open stack turning hardware-decoded video black once a
                # subtitle composites). Users who want hardware decoding can opt
                # in from Settings > Playback (or DOPEIPTV_HWDEC). When enabled,
                # 'auto-copy-safe' decodes on the GPU and copies the frame back
                # to RAM for upload as a normal texture, which works with any GL
                # context and any GPU that has a decoder.
                # Priority: env override > user setting > software default.
                "hwdec": (os.environ.get("DOPEIPTV_HWDEC")
                          or getattr(self, "hwdec_pref", None)
                          or "no"),
                # Safety net for those who opt into hardware decoding: if the
                # hardware DECODER fails (unsupported codec on this GPU, a
                # corrupt stream, a driver hiccup), drop to software after a
                # single failed frame instead of showing garbage. Note this only
                # catches decoder failures - it does NOT catch render-path GL
                # errors (e.g. the nvidia-open INVALID_ENUM where the decoder
                # succeeds but the texture upload corrupts), which is why
                # software stays the default rather than hardware+fallback.
                "hwdec_software_fallback": "1",
                # (No 'osc' option: the on-screen controller is a feature of
                # the standalone mpv GUI and doesn't exist in the libmpv/render
                # build we use - setting it only logged a harmless skip.)
                # Network resilience for live streams: abort a dead connection
                # instead of hanging forever (that then surfaces as an error we
                # auto-reconnect from), and let ffmpeg transparently reconnect
                # dropped HTTP streams. Any option a minimal libmpv build
                # rejects is skipped by the per-key loop below.
                "network-timeout": 30,
                "demuxer-lavf-o": "reconnect=1,reconnect_streamed=1,"
                                  "reconnect_on_network_error=1,"
                                  "reconnect_delay_max=5",
                # Faster channel switching: ffmpeg otherwise probes up to ~5 s /
                # 5 MB of a live MPEG-TS stream before it shows the first frame.
                # A live TS declares its codecs up front, so a shorter probe
                # reaches the picture much sooner with no practical downside.
                # Tunable/disable-able via env (seconds / bytes; 0 = ffmpeg's
                # own default) for streams that need deeper probing.
                "demuxer-lavf-analyzeduration": _env_num(
                    "DOPEIPTV_ANALYZEDURATION", 1.0, float),
                "demuxer-lavf-probesize": _env_num(
                    "DOPEIPTV_PROBESIZE", 2_000_000, int),
                # Never let mpv open its own window, and keep its OSD silent -
                # otherwise it draws the media title centred on black while a
                # stream buffers, which can surface as a stray frame.
                "force-window": "no", "osd-level": 0}
        if sys.platform == "darwin":
            from ..core.platform_macos import extra_mpv_opts
            soft.update(extra_mpv_opts())
        # Demuxer cache: default to mpv's own buffering (what standalone mpv
        # uses). A hard byte cap here bounds RAM but also removes the cushion a
        # high-bitrate VBR 4K stream needs - too small a cap starves the
        # decoder on bitrate spikes and shows up as a periodic ~10s hitch that
        # external mpv (bigger default cache) never has. Only cap when the user
        # explicitly opts in via DOPEIPTV_DEMUX_MAX / DOPEIPTV_DEMUX_MAX_BACK
        # (e.g. "192MiB"), trading a little smoothness headroom for less RAM.
        _dmax = os.environ.get("DOPEIPTV_DEMUX_MAX")
        _dback = os.environ.get("DOPEIPTV_DEMUX_MAX_BACK")
        if _dmax:
            soft["demuxer-max-bytes"] = _dmax
        if _dback:
            soft["demuxer-max-back-bytes"] = _dback
        # Video timing. Default is mpv's own (audio) sync - the most tested,
        # timing-tolerant mode, which suits our QOpenGLWidget surface (Qt
        # composites the frame, so we can't guarantee the exact display timing
        # that video-sync=display-resample needs). Enthusiasts can opt into a
        # display-sync mode with DOPEIPTV_VIDEO_SYNC=display-resample.
        _vsync = os.environ.get("DOPEIPTV_VIDEO_SYNC")
        if _vsync:
            soft["video-sync"] = _vsync
        soft.update(self.EXTRA_OPTS)
        for key, val in soft.items():
            try:
                self.mpv[key.replace("_", "-")] = val
            except Exception as e:
                log.debug("mpv option %r skipped: %s", key, e)
        # The Nerd page's options last (nothing while Nerd is off); one this
        # libmpv rejects is skipped, never fatal.
        try:
            from ..core import nerd
            nerd.apply_core(self.mpv)
        except Exception as e:
            log.warning("nerd options not applied: %s", e)
        log.info("mpv created, creating render context...")
        self._create_render_context()
        _register_error_callback(self.mpv, self.playback_error, self.eof_event)

    def _create_render_context(self) -> None:
        """Build the mpv OpenGL render context against the CURRENT GL context.
        Split out from _build_mpv_render so it can be rebuilt when the GL
        context is recreated (widget reparent / monitor change) WITHOUT
        recreating the mpv instance - the stream and audio never stop."""
        self._proc_address_fn = _libmpv.MpvGlGetProcAddressFn(
            self._get_proc_address)
        self._ctx = _libmpv.MpvRenderContext(
            self.mpv, "opengl",
            opengl_init_params={
                "get_proc_address": self._proc_address_fn})
        # mpv fires this render-update callback from its OWN render thread.
        # On Linux/macOS emitting a Qt signal from there is fine (Qt posts a
        # queued repaint). On Windows that cross-thread signal emit access-
        # violates inside libmpv's callback wrapper, so there we set no update
        # callback and instead drive repaints from a main-thread timer
        # (poll-render): slightly more GPU when idle, but rock-solid.
        if sys.platform == "win32":
            if getattr(self, "_render_timer", None) is None:
                self._render_timer = QTimer(self)
                self._render_timer.timeout.connect(self.update)
            self._render_timer.start(16)          # ~60 fps
        else:
            self._ctx.update_cb = lambda: self.frame_ready.emit()

    def _free_render_context(self, reason: str = "aboutToBeDestroyed") -> None:
        """Free just the render context - called when its GL context is about
        to be destroyed (reparent, close), and from shutdown. Never touches the
        mpv instance, so playback/audio continue; paintGL paints black while
        _ctx is None, and the next initializeGL rebuilds it."""
        ctx = self._ctx
        self._ctx = None
        if ctx is not None or self.mpv is not None:
            log.info("VID render context free (%s): had_ctx=%s glctx=%s",
                     reason, ctx is not None,
                     self._ctx_id(QOpenGLContext.currentContext()))
        if getattr(self, "_render_timer", None) is not None:
            self._render_timer.stop()
        if ctx is not None:
            try:
                ctx.free()
            except Exception as e:
                log.info("VID render context free (%s): ctx.free failed: %s",
                         reason, e)

    def _paint_mirror(self) -> None:
        """Render the OWNER's mpv render context into this mirror's own FBO
        (macOS pop-out). Only one of the two surfaces is on screen at a time
        (the docked one is hidden while popped out), so mpv still sees one
        present per frame. Never touches mpv or the owner's context - if the
        owner is between streams (ctx None) or blanked, paint black."""
        owner = self._mirror_of
        ctx = owner._ctx if owner is not None else None
        if self._blank or ctx is None:
            glctx = QOpenGLContext.currentContext()
            if glctx is not None:
                try:
                    f = glctx.functions()
                    f.glClearColor(0.0, 0.0, 0.0, 1.0)
                    f.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT
                except Exception:
                    pass
            return
        try:
            ratio = (self.devicePixelRatioF()
                     if hasattr(self, "devicePixelRatioF") else 1)
            ctx.render(flip_y=True, opengl_fbo={
                "w": int(self.width() * ratio),
                "h": int(self.height() * ratio),
                "fbo": self.defaultFramebufferObject(),
            })
            # The mirror is the SOLE renderer while popped out (the docked
            # surface is suspended), so it reports frame timing to mpv. Its
            # poll-render timer calls this at the display refresh, the standard
            # present cadence.
            try:
                ctx.report_swap()
            except Exception:
                pass
            if getattr(self, "_paint_state", "") != "render":
                self._paint_state = "render"
                log.info("VID mirror paint -> render (glctx=%s fbo=%s "
                         "size=%dx%d)",
                         self._ctx_id(QOpenGLContext.currentContext()),
                         self.defaultFramebufferObject(),
                         self.width(), self.height())
            # Heartbeat, as the docked surface has. The mirror had none, so a
            # log taken during a frozen fullscreen simply went quiet after
            # "paint -> render" and could not say whether paints had stopped
            # or the picture had merely stopped reaching the screen.
            self._mpaint_n = getattr(self, "_mpaint_n", 0) + 1
            if self._mpaint_n == 1:
                self._mpaint_at = time.monotonic()
            if self._mpaint_n % 300 == 0:
                now = time.monotonic()
                since = now - getattr(self, "_mpaint_at", now)
                self._mpaint_at = now
                src = self._mirror_of
                m = getattr(src, "mpv", None) if src is not None else None

                def _p(name):
                    try:
                        v = getattr(m, name)
                        return "none" if v is None else v
                    except Exception as e:
                        return f"err:{type(e).__name__}"
                log.debug("VID mirror alive #%d %.1f/s size=%dx%d fbo=%s "
                          "frame=%s vf-fps=%s",
                          self._mpaint_n,
                          (300 / since if since > 0 else 0.0),
                          self.width(), self.height(),
                          self.defaultFramebufferObject(),
                          _p("estimated_frame_number"),
                          _p("estimated_vf_fps"))
        except Exception as e:
            if getattr(self, "_paint_state", "") != "fail":
                self._paint_state = "fail"
                log.info("VID mirror paint -> FAIL: %s (glctx=%s)", e,
                         self._ctx_id(QOpenGLContext.currentContext()))

    def paintGL(self) -> None:
        if self._mirror_of is not None:
            self._paint_mirror()
            return
        if self._render_suspended:
            # A macOS mirror pop-out is presenting this stream; the docked
            # surface (covered by a placeholder) skips the mpv render so the
            # GPU isn't doing it twice. Clear to black and return - mpv is
            # untouched.
            glctx = QOpenGLContext.currentContext()
            if glctx is not None:
                try:
                    f = glctx.functions()
                    f.glClearColor(0.0, 0.0, 0.0, 1.0)
                    f.glClear(0x00004000)
                except Exception:
                    pass
            return
        # Video-trace heartbeat: proves whether paints happen AT ALL while
        # the picture looks frozen (paints running but a stale image on
        # screen = compositing problem; no paints = dead repaint loop).
        # One line per ~300 frames, debug level only.
        self._paint_n = getattr(self, "_paint_n", 0) + 1
        if self._paint_n == 1:
            # Start the clock on the first paint, not the first heartbeat:
            # without this the opening line divided by a zero interval and
            # always read 0.0/s, which looks exactly like a dead repaint
            # loop in a log you are reading to find a dead repaint loop.
            self._paint_hb_at = time.monotonic()
        if self._paint_n % 300 == 0:
            # frame is the decisive freeze diagnostic: the number of the
            # frame mpv is on. Advancing across heartbeats while the
            # on-screen picture is frozen = mpv makes new frames and the
            # freeze is presentation/compositing; stuck = the video track
            # itself stalled (audio continues) and the decoder path (e.g.
            # videotoolbox) is the suspect. vf-fps is the same story as a
            # rate, and collapses toward 0 the moment output stops.
            #
            # (These replaced "video-pts", which mpv has no such property
            # for - every heartbeat logged err:AttributeError, which is
            # error code -8, "mpv property does not exist".)
            def _prop(name: str):
                # One property per try: a single unsupported name must not
                # blank the whole probe (the first log run showed None for
                # all three because the first read raised). err:<Type> in
                # the log distinguishes "unsupported" from a None value.
                try:
                    v = getattr(self.mpv, name)
                    return "none" if v is None else v
                except Exception as e:
                    return f"err:{type(e).__name__}"
            # Paints per second matters as much as the count: a background
            # window that macOS has throttled still paints, just far slower,
            # and that is what separates "the OS is starving us" from "the
            # provider dropped the stream".
            now = time.monotonic()
            since = now - getattr(self, "_paint_hb_at", now)
            self._paint_hb_at = now
            log.debug("VID paint alive #%d %.1f/s state=%s fbo=%s "
                      "frame=%s vf-fps=%s drops=%s cache=%s hwdec=%s",
                      self._paint_n, (300 / since if since > 0 else 0.0),
                      getattr(self, "_paint_state", "?"),
                      self.defaultFramebufferObject(),
                      _prop("estimated_frame_number"),
                      _prop("estimated_vf_fps"),
                      _prop("frame_drop_count"),
                      _prop("demuxer_cache_time"), _prop("hwdec_current"))
        # Blank branch first so a repaint that arrives mid-stop (when mpv has
        # already been told to stop but our _ctx is still around) doesn't try
        # to render an mpv frame with a half-torn-down context - which
        # crashes on Wayland. Also guard the render path against a null ctx,
        # a null current GL context, or an exception bubbling out of libmpv,
        # so any transient race becomes a black frame instead of a segfault.
        if self._blank or self._ctx is None:
            # Self-heal a lost render context. It's freed when this widget's GL
            # context is destroyed on a reparent (pop-out dock/undock); normally
            # the following initializeGL rebuilds it, but on macOS (shared GL
            # contexts) initializeGL doesn't always re-fire, so _ctx stays None
            # and the video is stuck black while audio keeps playing - only a
            # fullscreen round-trip brought it back. Rebuild it here against the
            # now-current context instead. Throttled so a genuinely broken GL
            # stack can't spin, and only while actually playing (mpv alive, not
            # a deliberate stop-blank). mpv is never touched.
            if (not self._blank and self._ctx is None and self.mpv is not None
                    and QOpenGLContext.currentContext() is not None
                    and time.monotonic() - getattr(self, "_ctx_heal_at", 0.0)
                    > 0.5):
                self._ctx_heal_at = time.monotonic()
                try:
                    self._create_render_context()
                    log.info("render context self-healed in paintGL")
                except Exception as e:
                    self._ctx = None
                    log.debug("paintGL render-context heal failed: %s", e)
            if self._blank or self._ctx is None:
                st = "blank" if self._blank else "no-ctx"
                if getattr(self, "_paint_state", "") != st:
                    self._paint_state = st
                    log.info("VID paint -> %s (blank=%s mpv=%s glctx=%s "
                             "size=%dx%d)", st, self._blank,
                             self.mpv is not None,
                             self._ctx_id(QOpenGLContext.currentContext()),
                             self.width(), self.height())
                glctx = QOpenGLContext.currentContext()
                if glctx is not None:
                    try:
                        f = glctx.functions()
                        f.glClearColor(0.0, 0.0, 0.0, 1.0)
                        f.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT
                    except Exception:
                        pass
                return
        try:
            ratio = (self.devicePixelRatioF()
                     if hasattr(self, "devicePixelRatioF") else 1)
            self._ctx.render(flip_y=True, opengl_fbo={
                "w": int(self.width() * ratio),
                "h": int(self.height() * ratio),
                "fbo": self.defaultFramebufferObject(),
            })
            if getattr(self, "_paint_state", "") != "render":
                # First successful mpv render after blank/failure/startup:
                # the glctx + fbo identities here are the trace's ground
                # truth for what the video is actually being drawn into.
                self._paint_state = "render"
                self._paint_fail_n = 0
                log.info("VID paint -> render (glctx=%s fbo=%s size=%dx%d "
                         "dpr=%.1f)",
                         self._ctx_id(QOpenGLContext.currentContext()),
                         self.defaultFramebufferObject(),
                         self.width(), self.height(), ratio)
            # Tell mpv a frame was just presented. Without this it can't
            # measure the real display refresh, so its frame-timing estimate
            # drifts and high-fps / 4K content judders and drops frames. The
            # buffer swap happens right after paintGL returns, so reporting
            # here (once per painted frame) is the standard approximation.
            try:
                self._ctx.report_swap()
            except Exception:
                pass
        except Exception as e:
            # Skip just THIS frame - never latch _blank here. A transient
            # render failure during a reparent (the render context being torn
            # down / rebuilt as the player docks in and out of the pop-out
            # window) would otherwise blank the video permanently: it stayed
            # black through channel changes and stops, and only a fullscreen
            # round-trip (which clears the flag) brought it back. The next
            # paint just retries against the rebuilt context; the framebuffer
            # keeps the last good frame until then.
            self._paint_fail_n = getattr(self, "_paint_fail_n", 0) + 1
            if (getattr(self, "_paint_state", "") != "fail"
                    or self._paint_fail_n % 120 == 0):
                self._paint_state = "fail"
                log.info("VID paint -> fail #%d: %s (glctx=%s fbo=%s)",
                         self._paint_fail_n, e,
                         self._ctx_id(QOpenGLContext.currentContext()),
                         self.defaultFramebufferObject())
            else:
                log.debug("paintGL render failed (frame skipped): %s", e)

    def resizeGL(self, w: int, h: int) -> None:
        # Part of the video trace: shows whether a maximize/reparent actually
        # reaches the GL widget as a resize, and which framebuffer it lands
        # in. Debug level - a few lines per window transition.
        super().resizeGL(w, h)
        log.debug("VID resizeGL %dx%d fbo=%s glctx=%s", w, h,
                  self.defaultFramebufferObject(),
                  self._ctx_id(QOpenGLContext.currentContext()))
        if self._mirror_of is not None and sys.platform == "darwin":
            # Going fullscreen resizes this surface several times as the
            # animation runs (720x388 -> 1512x917 -> 1512x949 -> 1512x917 in
            # the report). Settle it once the resizes stop - see
            # _settle_mirror_surface for why.
            self._settle_mirror_soon()

    def _settle_mirror_soon(self) -> None:
        # The settle resizes this widget, which comes straight back here as
        # another resizeGL. Without these two guards that re-arms the timer
        # and the whole thing feeds itself at 4 Hz - 89 settles in one
        # session, each doing a synchronous full-window repaint, which is
        # visible as stutter in fullscreen.
        if getattr(self, "_settling", False):
            return                                  # our own nudge
        if (self.width(), self.height()) == getattr(self, "_settled_size",
                                                    None):
            return                                  # already settled here
        t = getattr(self, "_settle_timer", None)
        if t is None:
            t = self._settle_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._settle_mirror_surface)
        # Short on purpose. The settle is not overhead ahead of the freeze -
        # it is the CURE for it, so every millisecond of debounce is a
        # millisecond of frozen picture. Measured on the reported stall:
        # 2.2 settles per transition at 450 ms of waiting plus ~111 ms of
        # work each, which is the "1-2 seconds" almost exactly. macOS goes
        # fullscreen in two steps (1512x917, then 1512x949 as the menu bar
        # hides) and both want curing, so collapsing them into one late
        # settle is worse than doing two prompt ones.
        #
        # It cannot run away: _settling ignores the resizes the settle
        # itself causes, and _settled_size ignores arriving at a size
        # already cured. Real resizes per transition are a handful.
        t.start(60)           # re-armed by each resize; fires after the last

    def _settle_mirror_surface(self) -> None:
        """Rebuild this surface's backing store after a macOS fullscreen
        transition.

        The player already carries this remedy for the pop-out reparent, in
        _settle_after_reparent: on macOS a GL widget's backing framebuffer
        can stay bound to the OLD window and keep showing a frozen frame
        while rendering reports success the whole time. Entering fullscreen
        moves this mirror into a new window in exactly the same way, and
        that path never got the fix - which matches the report: picture
        frozen, audio fine, mpv rendering without a single error.

        Pure widget geometry. mpv, the render context and the docked surface
        are not touched."""
        self._settling = True
        try:
            t0 = time.monotonic()
            sz = self.size()
            if sz.isValid() and sz.height() > 1:
                self.resize(sz.width(), sz.height() - 1)
                self.resize(sz)
            self._settled_size = (self.width(), self.height())
            self.update()
            t1 = time.monotonic()
            win = self.window()
            if win is not None:
                win.repaint()      # synchronous: an async update can lose
                                   # the race with the stale layer
            t2 = time.monotonic()
            # Timed because the remedy is not free and the log has no
            # timestamps: nudge and repaint are reported apart so a stall at
            # the transition can be blamed on the right one - or on neither,
            # which would put it on the mirror teardown/rebuild instead.
            log.info("VID mirror settled (%dx%d) nudge=%.0fms repaint=%.0fms",
                     sz.width(), sz.height(),
                     (t1 - t0) * 1000, (t2 - t1) * 1000)
        except Exception as e:
            log.debug("mirror settle failed: %s", e)
        finally:
            # Cleared next time round the event loop, so a resizeGL our own
            # nudge delivers asynchronously is still recognised as ours.
            QTimer.singleShot(0, self._settle_done)

    def _settle_done(self) -> None:
        self._settling = False

    def shutdown(self) -> None:
        self._free_render_context("shutdown")
        if self.mpv:
            try:
                self.mpv.terminate()
            except Exception:
                pass
            self.mpv = None


class _RasterMirror(QWidget):
    """Linux pop-out mirror: a PLAIN RASTER widget fed CPU images.

    On this user's stack (modern Qt/Mesa, X11 and Wayland alike) every GL
    presentation path in a SECOND top-level window shows black even though
    rendering reports success: a reparented QOpenGLWidget, a second
    QOpenGLWidget mirror, and a native QOpenGLWindow subsurface all failed
    identically. Raster widgets, meanwhile, provably DO show there (the
    control bar does). So the pop-out video is painted as images: each mirror
    tick renders mpv into an offscreen FBO in the DOCKED widget's GL context
    (the one context that demonstrably presents) and blits the readback here.
    The docked mpv instance and render context are never touched, moved or
    freed - nothing in the playback path can break. Cost: a GPU->CPU readback
    per frame, capped at ~30 fps and sized to the pop-out."""

    video_mouse_press = pyqtSignal(object)
    video_mouse_move = pyqtSignal(object)
    video_mouse_release = pyqtSignal(object)
    video_dbl_click = pyqtSignal()

    def __init__(self, parent: QWidget | None = None,
                 mirror_of: "_MpvGLWidget | None" = None) -> None:
        super().__init__(parent)
        self._mirror_of = mirror_of
        # Parity with the GL mirrors (tests + teardown code paths).
        self.mpv = None
        self._ctx = None
        self._img: QImage | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_image(self, img: "QImage") -> None:
        self._img = img
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        img = self._img
        if img is not None and not img.isNull():
            # The frame is rendered at the widget size capped to 1080p, so
            # large pop-outs upscale here - smooth, or video looks blocky.
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawImage(self.rect(), img)
        p.end()

    # Same signal surface as the GL mirrors - identical pop-out wiring.
    def mousePressEvent(self, e) -> None:
        self.video_mouse_press.emit(e)

    def mouseMoveEvent(self, e) -> None:
        self.video_mouse_move.emit(e)

    def mouseReleaseEvent(self, e) -> None:
        self.video_mouse_release.emit(e)

    def mouseDoubleClickEvent(self, _e) -> None:
        self.video_dbl_click.emit()
