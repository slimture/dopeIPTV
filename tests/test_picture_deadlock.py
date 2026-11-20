"""Pictures decoded on workers while the window's thread loads its own.

A picture's handler is found under one Qt lock for every image plugin, and
each plugin asks the device; our QBuffer answers through sip, which takes
the GIL. ``QImageReader.read()`` lets go of the GIL, so left to it a worker
held Qt's lock and waited for the GIL while the window's thread - an icon,
a picture - held the GIL and waited for Qt's lock: the app stopped for good
(a macOS hang report, 1.6.8; the same on Linux). The handler is found first
with the GIL held (``workers.ready``).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_RUN = textwrap.dedent("""
    import os, sys, threading, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, sys.argv[1])
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QGuiApplication, QImage
    app = QGuiApplication([])
    from dopeiptv.core.workers import checked_picture, decode_image
    img = QImage(32, 32, QImage.Format.Format_RGB32)
    img.fill(QColor(10, 20, 30))
    ba = QByteArray()
    b = QBuffer(ba)
    b.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(b, "PNG")
    b.close()
    png = bytes(ba)
    stop = False
    bad = []
    def work(fn):
        while not stop:
            if fn(png) is None:
                bad.append(fn.__name__)
                return
    ts = [threading.Thread(target=work, args=(fn,), daemon=True)
          for fn in (decode_image, decode_image, checked_picture, decode_image)]
    for t in ts:
        t.start()
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        QImage.fromData(png)
    stop = True
    for t in ts:
        t.join()
    print("bad" if bad else "ok")
""")


def test_workers_and_the_window_decode_at_once():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        out = subprocess.run([sys.executable, "-c", _RUN, str(ROOT)], env=env,
                             capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise AssertionError("pictures decoded on workers stopped the "
                             "window's thread (Qt's lock against the GIL)") from None
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok", out.stdout + out.stderr


def test_every_reader_of_a_python_device_finds_its_handler_first():
    """A QImageReader on a QBuffer made here is ``ready`` before it reads:
    no file that makes both reads without it."""
    for path in sorted((ROOT / "dopeiptv").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "QBuffer(" not in src or not re.search(r"QImageReader\(", src):
            continue
        assert "ready(" in src, f"{path.relative_to(ROOT)}: a reader without ready()"
