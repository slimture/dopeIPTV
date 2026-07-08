# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for dopeIPTV on macOS (the .app / .dmg).

Differs from dopeiptv.spec in two macOS-specific ways: it locates libmpv from
the Homebrew prefix (Apple Silicon or Intel) and finishes with a BUNDLE step
that produces dopeIPTV.app with a proper Info.plist. Linux and Windows keep
using the plain dopeiptv.spec - this file is only invoked by the macos-dmg
CI job.
"""

import ctypes.util
import glob
import os
import shutil

from PyInstaller.utils.hooks import collect_all


APP_NAME = "dopeIPTV"
BUNDLE_ID = "io.github.slimture.dopeIPTV"
VERSION = os.environ.get("DOPEIPTV_VERSION", "0.0.0")


def _find_ffmpeg():
    exe = shutil.which("ffmpeg")
    return [(exe, ".")] if exe else []


def _find_libmpv():
    """libmpv on macOS lives under Homebrew's lib dir; probe both prefixes."""
    candidates = []
    name = ctypes.util.find_library("mpv")
    if name and os.path.isabs(name):
        candidates.append(name)
    for d in ("/opt/homebrew/lib", "/usr/local/lib"):
        candidates += glob.glob(os.path.join(d, "libmpv*.dylib"))
    for path in candidates:
        if path and os.path.exists(path):
            return [(path, ".")]
    print("WARNING: libmpv not found; embedded playback will be dead.")
    return []


binaries = _find_libmpv() + _find_ffmpeg()
datas = []
hiddenimports = ["mpv"]

for pkg in ("pychromecast", "zeroconf"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += [
    "PyQt6.QtOpenGLWidgets",
    "PyQt6.QtNetwork",
]


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
    upx=False,      # signing/notarising doesn't like UPX-packed binaries.
    console=False,  # LSUIElement=False, but no Terminal window.
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
    upx=False,
    upx_exclude=[],
    name='dopeiptv',
)

# --- .app bundle ----------------------------------------------------------
# The Info.plist keys that actually matter for user perception:
#   CFBundleName / CFBundleDisplayName - the app-menu label (fixes "python").
#   CFBundleShortVersionString / CFBundleVersion - shown in About and Finder.
#   LSMinimumSystemVersion - macOS floor; keep in step with the runner image.
#   NSHighResolutionCapable - Retina rendering (mandatory on modern macOS).
app = BUNDLE(
    coll,
    name=f'{APP_NAME}.app',
    icon=os.environ.get("DOPEIPTV_ICNS") or None,
    bundle_identifier=BUNDLE_ID,
    version=VERSION,
    info_plist={
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': APP_NAME,
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
        'NSPrincipalClass': 'NSApplication',
        'NSHumanReadableCopyright': 'GPL-3.0-or-later',
        'LSApplicationCategoryType': 'public.app-category.video',
    },
)
