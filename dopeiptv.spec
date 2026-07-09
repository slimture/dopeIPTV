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
import sys
import tempfile

from PyInstaller.utils.hooks import collect_all


def _find_ffmpeg():
    """Bundle the ffmpeg binary so recording works without a system install."""
    exe = shutil.which("ffmpeg")
    if exe:
        return [(exe, ".")]
    print("WARNING: ffmpeg not found; recording will need a system ffmpeg.")
    return []


def _find_libmpv():
    """Bundle libmpv under the exact soname python-mpv dlopen()s.

    python-mpv loads the library by a versioned soname (libmpv.so.2 on
    Linux, libmpv.2.dylib on macOS). Two things have to be right or the
    embedded player is dead in the packaged app:

      * we must actually find it - on Apple Silicon Homebrew installs to
        /opt/homebrew/lib, which the old probe missed, so the .dmg
        shipped with no libmpv at all;
      * we must bundle it under that versioned soname, not the bare
        libmpv.so dev symlink - PyInstaller names the bundled file after
        the source basename, and a bundle with only "libmpv.so" fails a
        CDLL("libmpv.so.2") lookup at runtime.

    So we locate the library, resolve any symlink to the real file, and
    stage a copy named after the primary soname before handing it to
    PyInstaller (which then also pulls in its transitive deps)."""
    if sys.platform == "darwin":
        primary = "libmpv.2.dylib"
        prefer = ("libmpv.2.dylib", "libmpv.dylib")
        patterns = [
            "/opt/homebrew/lib/libmpv*.dylib",
            "/usr/local/lib/libmpv*.dylib",
            "/opt/homebrew/Cellar/mpv/*/lib/libmpv*.dylib",
            "/usr/local/Cellar/mpv/*/lib/libmpv*.dylib",
            "/opt/local/lib/libmpv*.dylib",
        ]
    else:
        primary = "libmpv.so.2"
        prefer = ("libmpv.so.2", "libmpv.so.1", "libmpv.so")
        patterns = [
            "/usr/lib/x86_64-linux-gnu/libmpv.so*",
            "/lib/x86_64-linux-gnu/libmpv.so*",
            "/usr/lib/aarch64-linux-gnu/libmpv.so*",
            "/usr/lib/libmpv.so*",
            "/usr/lib64/libmpv.so*",
            "/usr/local/lib/libmpv.so*",
        ]
    matches: list[str] = []
    for pat in patterns:
        matches += glob.glob(pat)
    chosen = None
    for name in prefer:                       # prefer the versioned soname
        for m in matches:
            if os.path.basename(m) == name:
                chosen = m
                break
        if chosen:
            break
    if not chosen and matches:
        chosen = matches[0]
    if not chosen:                            # last resort: the linker
        name = ctypes.util.find_library("mpv")
        if name and os.path.exists(name):
            chosen = name
    if not chosen:
        print("WARNING: libmpv not found; the embedded player will not work "
              "in this build. Install mpv/libmpv before running PyInstaller.")
        return []
    real = os.path.realpath(chosen)
    staged = os.path.join(
        tempfile.mkdtemp(prefix="dopeiptv-libmpv-"), primary)
    shutil.copy2(real, staged)
    print(f"Bundling libmpv: {real} -> {primary}")
    return [(staged, ".")]


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
