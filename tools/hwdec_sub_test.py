#!/usr/bin/env python3
"""Minimal reproduction / bisect harness for the "subtitle turns 4K video black"
bug on the embedded libmpv render API.

It builds the SAME player core the app uses (vo=libmpv + the OpenGL render API +
the same extra mpv options), plays a file you pass, and after 4 s automatically
enables the first subtitle track - the moment the app's video goes black. Then
you flip options off one at a time to find the culprit.

Run the app-equivalent setup first (should reproduce the black):

    python3 tools/hwdec_sub_test.py /path/to/4k-movie.mkv

Then drop suspect options one at a time until the picture stays visible after
the subtitle switches on:

    python3 tools/hwdec_sub_test.py FILE --no-tonemapping
    python3 tools/hwdec_sub_test.py FILE --no-sharpen
    python3 tools/hwdec_sub_test.py FILE --no-deinterlace
    python3 tools/hwdec_sub_test.py FILE --no-osd0        # use osd-level=1
    python3 tools/hwdec_sub_test.py FILE --no-aspect
    python3 tools/hwdec_sub_test.py FILE --no-report-swap
    python3 tools/hwdec_sub_test.py FILE --no-cache
    python3 tools/hwdec_sub_test.py FILE --minimal        # drop ALL extras

hwdec mode (default matches the app's current default):

    python3 tools/hwdec_sub_test.py FILE --hwdec auto-safe
    python3 tools/hwdec_sub_test.py FILE --hwdec no

Whichever single --no-X makes the picture survive the subtitle switch is the
option responsible. Tell me which one and I fix it in the app.
"""
from __future__ import annotations

import argparse
import sys

from PyQt6.QtCore import QByteArray, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QOpenGLContext
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QApplication

import mpv


class GL(QOpenGLWidget):
    frame_ready = pyqtSignal()

    def __init__(self, args):
        super().__init__()
        self._args = args
        self.mpv = None
        self._ctx = None
        self.frame_ready.connect(self.update)
        self.setMinimumSize(960, 540)

    def _get_proc_address(self, _, name):
        glctx = QOpenGLContext.currentContext()
        if glctx is None:
            return 0
        addr = glctx.getProcAddress(QByteArray(name))
        return int(addr) if addr else 0

    def initializeGL(self):
        a = self._args
        glctx = QOpenGLContext.currentContext()
        if glctx is not None:
            f = glctx.format()
            v = f.version()
            print(f"[test] GL context: {v[0]}.{v[1]} "
                  f"profile={f.profile().name} "
                  f"renderableType={f.renderableType().name}")

        def _log(level, prefix, text):
            sys.stderr.write(f"[mpv/{level}] {prefix}: {text}")
            sys.stderr.flush()

        if a.verbose:
            self.mpv = mpv.MPV(vo="libmpv", terminal=False,
                               log_handler=_log, loglevel="v")
        else:
            self.mpv = mpv.MPV(vo="libmpv", terminal=False)

        def opt(k, v):
            try:
                self.mpv[k] = v
                print(f"[test] set {k}={v}")
            except Exception as e:
                print(f"[test] FAILED {k}={v}: {e}")

        opt("hwdec", a.hwdec)
        opt("keep-open", "yes")
        if a.dr:
            opt("vd-lavc-dr", a.dr)
        if a.blend:
            opt("blend-subtitles", a.blend)
        if not a.minimal:
            if not a.no_osd0:
                opt("osd-level", 0)
            if not a.no_cache:
                opt("cache", "yes")
                opt("cache-secs", 10.0)
            if not a.no_deinterlace:
                opt("deinterlace", False)
            if not a.no_sharpen:
                opt("sharpen", 0.0)
            if not a.no_tonemapping:
                opt("tone-mapping", "auto")
            if not a.no_aspect:
                opt("keepaspect", True)
                opt("video-aspect-override", "-1")
        self._proc = mpv.MpvGlGetProcAddressFn(self._get_proc_address)
        self._ctx = mpv.MpvRenderContext(
            self.mpv, "opengl",
            opengl_init_params={"get_proc_address": self._proc})
        self._ctx.update_cb = lambda: self.frame_ready.emit()

    def paintGL(self):
        if self._ctx is None:
            return
        ratio = self.devicePixelRatioF()
        try:
            self._ctx.render(flip_y=True, opengl_fbo={
                "w": int(self.width() * ratio),
                "h": int(self.height() * ratio),
                "fbo": self.defaultFramebufferObject()})
        except BaseException as e:
            # Print once, keep going, don't crash - so we learn WHY it fails.
            if not getattr(self, "_render_err", False):
                self._render_err = True
                import traceback
                print(f"\n[test] !!! render() FAILED: {type(e).__name__}: {e}")
                traceback.print_exc()
                print("[test] (this is the moment the app shows black)\n")
            return
        if not self._args.no_report_swap:
            try:
                self._ctx.report_swap()
            except Exception:
                pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--hwdec", default="auto-copy-safe")
    p.add_argument("--dr", choices=("no", "auto", "yes"), default=None,
                   help="vd-lavc-dr (direct rendering); 'no' is the known fix "
                        "for copy-hwdec corruption in embedded render contexts")
    p.add_argument("--blend", choices=("no", "yes", "video"), default=None,
                   help="blend-subtitles: bake subs into the video frame "
                        "instead of the separate OSD pass")
    p.add_argument("--sid", type=int, default=None,
                   help="force a specific subtitle track id (see the list)")
    p.add_argument("--seek", type=float, default=None,
                   help="seek to this many seconds after enabling the sub, to "
                        "reach a point with dialogue")
    p.add_argument("--sw-on-sub", action="store_true",
                   help="switch to software decoding when the subtitle is "
                        "enabled (the proposed app fix)")
    p.add_argument("--verbose", action="store_true",
                   help="print mpv's own debug log - shows the real error when "
                        "the subtitle breaks the render")
    p.add_argument("--minimal", action="store_true")
    for flag in ("no-osd0", "no-cache", "no-deinterlace", "no-sharpen",
                 "no-tonemapping", "no-aspect", "no-report-swap"):
        p.add_argument(f"--{flag}", action="store_true")
    a = p.parse_args()

    if a.gl:
        # Force a real DESKTOP OpenGL context (not GLES). On NVIDIA/Wayland Qt
        # hands mpv an OpenGL ES context, and mpv's GLES path fails to create
        # the 16-bit video / OSD textures (INVALID_ENUM) -> black + subs. Both
        # the app-attribute AND the surface format are needed, set before the
        # QApplication exists.
        from PyQt6.QtGui import QSurfaceFormat
        QApplication.setAttribute(
            Qt.ApplicationAttribute.AA_UseDesktopOpenGL)
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
        QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)
    w = GL(a)
    w.setWindowTitle("hwdec+subtitle test - subtitle switches on after 4s")
    w.show()
    for _ in range(5):
        app.processEvents()
    w.mpv.play(a.file)

    def enable_sub():
        tl = w.mpv.track_list or []
        print("[test] tracks:")
        for t in tl:
            print(f"    type={t.get('type')} id={t.get('id')} "
                  f"lang={t.get('lang')} title={t.get('title')} "
                  f"codec={t.get('codec')}")
        subs = [t for t in tl if t.get("type") == "sub"]
        if not subs:
            print("[test] !! NO subtitle tracks in this file - nothing to test. "
                  "Use a file that has embedded subs (the one you saw break in "
                  "the app), then re-run.")
            return
        if a.sid is not None:
            sid = a.sid
        else:
            # Skip 'Forced' tracks - they only draw on foreign signs, so at the
            # start of a film nothing renders and nothing reproduces. Prefer a
            # full subtitle so text actually composites.
            full = [t for t in subs
                    if "forced" not in (t.get("title") or "").lower()]
            sid = (full or subs)[0].get("id")
        print(f"[test] >>> enabling subtitle sid={sid} "
              f"(lang={subs[0].get('lang')} codec={subs[0].get('codec')}) - "
              f"WATCH THE VIDEO NOW <<<")
        try:
            if a.sw_on_sub:
                # The fix under test: drop to software decoding the moment a
                # subtitle goes on (hardware decode + OSD composite is what
                # breaks). Set it just before enabling the sub.
                print("[test] switching hwdec=no for subtitle playback")
                w.mpv["hwdec"] = "no"
            w.mpv["sub-visibility"] = True
            w.mpv["sid"] = sid
            if a.seek is not None:
                w.mpv.command("seek", a.seek, "absolute")
                print(f"[test] seeking to {a.seek}s for dialogue")
        except Exception as e:
            print(f"[test] enabling sid={sid} failed: {e}")

    def status():
        m = w.mpv
        try:
            print(f"[test] status: hwdec-current={m['hwdec-current']} "
                  f"gpu-api={m.get('gpu-api', '?')} sid={m['sid']} "
                  f"dwidth={m['dwidth']} time={m['time-pos']:.1f} "
                  f"sub-text={m['sub-text']!r}")
        except Exception:
            pass

    QTimer.singleShot(4000, enable_sub)
    st = QTimer()
    st.timeout.connect(status)
    st.start(2000)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
