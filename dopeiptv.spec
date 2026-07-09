# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for dopeIPTV (used for the AppImage / onedir bundle).

python-mpv loads libmpv through ctypes at runtime, so PyInstaller's import
analysis never sees it - we have to locate the shared library ourselves and
add it to the bundle, otherwise the embedded player is dead on any machine
that doesn't already have libmpv installed.
"""

import ctypes.util
import glob
import os
import shutil

from PyInstaller.utils.hooks import collect_all


def _find_ffmpeg():
    """Bundle the ffmpeg binary so recording works without a system install."""
    exe = shutil.which("ffmpeg")
    if exe:
        return [(exe, ".")]
    print("WARNING: ffmpeg not found; recording will need a system ffmpeg.")
    return []


def _find_libmpv():
    """Return [(src, '.')] for the libmpv shared library, or [] if not found."""
    candidates = []
    name = ctypes.util.find_library("mpv")
    if name and os.path.isabs(name):
        candidates.append(name)
    # find_library often returns just a soname; probe the usual lib dirs too.
    for d in ("/usr/lib", "/usr/lib64", "/usr/local/lib",
              "/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu"):
        candidates += glob.glob(os.path.join(d, "libmpv.so*"))
        candidates += glob.glob(os.path.join(d, "libmpv.*.dylib"))
    for path in candidates:
        if path and os.path.exists(path):
            return [(path, ".")]
    print("WARNING: libmpv not found; the embedded player will not work in "
          "this build. Install mpv/libmpv before running PyInstaller.")
    return []


binaries = _find_libmpv() + _find_ffmpeg()
datas = []
hiddenimports = ["mpv"]

# pychromecast (and its zeroconf/protobuf deps) ship data files and submodules
# that need explicit collection.
for pkg in ("pychromecast", "zeroconf"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Qt submodules loaded dynamically (not always seen by the analysis).
hiddenimports += [
    "PyQt6.QtDBus",
    "PyQt6.QtOpenGLWidgets",
    "PyQt6.QtNetwork",
]

# Optional built-in TMDB key module (git-ignored, written by CI from a
# secret). It's imported dynamically inside a try/except, so name it here
# so PyInstaller bundles it when the release build has baked one.
import os as _os
if _os.path.exists(_os.path.join("dopeiptv", "_tmdb_key.py")):
    hiddenimports += ["dopeiptv._tmdb_key"]


a = Analysis(
    ['dopeiptv.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='dopeiptv',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='dopeiptv',
)
