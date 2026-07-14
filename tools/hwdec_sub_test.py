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
        self.mpv = mpv.MPV(vo="libmpv", terminal=False)

        def opt(k, v):
            try:
                self.mpv[k] = v
                print(f"[test] set {k}={v}")
            except Exception as e:
                print(f"[test] FAILED {k}={v}: {e}")

        opt("hwdec", a.hwdec)
        opt("keep-open", "yes")
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
        self._ctx.render(flip_y=True, opengl_fbo={
            "w": int(self.width() * ratio),
            "h": int(self.height() * ratio),
            "fbo": self.defaultFramebufferObject()})
        if not self._args.no_report_swap:
            try:
                self._ctx.report_swap()
            except Exception:
                pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--hwdec", default="auto-copy-safe")
    p.add_argument("--minimal", action="store_true")
    for flag in ("no-osd0", "no-cache", "no-deinterlace", "no-sharpen",
                 "no-tonemapping", "no-aspect", "no-report-swap"):
        p.add_argument(f"--{flag}", action="store_true")
    a = p.parse_args()

    app = QApplication(sys.argv)
    w = GL(a)
    w.setWindowTitle("hwdec+subtitle test - subtitle switches on after 4s")
    w.show()
    for _ in range(5):
        app.processEvents()
    w.mpv.play(a.file)

    def enable_sub():
        print("[test] >>> enabling subtitle (sid=1) NOW - watch the video <<<")
        try:
            w.mpv["sid"] = 1
        except Exception as e:
            print(f"[test] sid=1 failed: {e}; trying 'auto'")
            w.mpv["sid"] = "auto"

    QTimer.singleShot(4000, enable_sub)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
