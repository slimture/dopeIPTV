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
import tempfile

from PyInstaller.utils.hooks import collect_all, collect_data_files


APP_NAME = "dopeIPTV"
BUNDLE_ID = "io.github.slimture.dopeIPTV"
VERSION = os.environ.get("DOPEIPTV_VERSION", "0.0.0")


def _find_ffmpeg():
    # DOPEIPTV_FFMPEG: the Intel build's ffmpeg from conda-forge, which is
    # not on PATH (see release.yml).
    exe = os.environ.get("DOPEIPTV_FFMPEG") or shutil.which("ffmpeg")
    return [(exe, ".")] if exe and os.path.exists(exe) else []


def _find_libmpv():
    """Bundle libmpv under the exact soname python-mpv dlopen()s
    (libmpv.2.dylib), resolving the Homebrew symlink to the real file so
    the name is right regardless of the on-disk versioned name."""
    primary = "libmpv.2.dylib"
    prefer = ("libmpv.2.dylib", "libmpv.dylib")
    patterns = []
    # DOPEIPTV_LIBMPV_DIR: the Intel build's libmpv from conda-forge (see
    # release.yml) - looked at first.
    if os.environ.get("DOPEIPTV_LIBMPV_DIR"):
        patterns.append(os.path.join(os.environ["DOPEIPTV_LIBMPV_DIR"],
                                     "libmpv*.dylib"))
    patterns += [
        "/opt/homebrew/lib/libmpv*.dylib",
        "/usr/local/lib/libmpv*.dylib",
        "/opt/homebrew/Cellar/mpv/*/lib/libmpv*.dylib",
        "/usr/local/Cellar/mpv/*/lib/libmpv*.dylib",
        "/opt/local/lib/libmpv*.dylib",
    ]
    matches = []
    for pat in patterns:
        matches += glob.glob(pat)
    chosen = None
    for name in prefer:
        for m in matches:
            if os.path.basename(m) == name:
                chosen = m
                break
        if chosen:
            break
    if not chosen and matches:
        chosen = matches[0]
    if not chosen:
        name = ctypes.util.find_library("mpv")
        if name and os.path.exists(name):
            chosen = name
    if not chosen:
        print("WARNING: libmpv not found; embedded playback will be dead.")
        return []
    real = os.path.realpath(chosen)
    if os.environ.get("DOPEIPTV_LIBMPV_DIR") and \
            os.path.basename(real) == primary:
        # conda-forge's, already under the name python-mpv loads: bundle it
        # from where it is, so its @loader_path rpath still finds its
        # libraries for PyInstaller - a copy elsewhere would leave them all
        # behind. (Homebrew's links its libraries by absolute path; it is
        # staged as before.)
        print(f"Bundling libmpv: {real}")
        return [(real, ".")]
    staged = os.path.join(
        tempfile.mkdtemp(prefix="dopeiptv-libmpv-"), primary)
    shutil.copy2(real, staged)
    print(f"Bundling libmpv: {real} -> {primary}")
    return [(staged, ".")]


binaries = _find_libmpv() + _find_ffmpeg()
datas = []
# Our own package data: the add-on locale JSONs (i18n loads them at import).
datas += collect_data_files('dopeiptv')
# Belt-and-suspenders: add the locale JSONs explicitly too, so the languages
# ship even if collect_data_files misses them. Without these the picker
# silently collapses to English-only in the frozen app.
datas += [(f, 'dopeiptv/locale') for f in glob.glob('dopeiptv/locale/*.json')]
datas += [(f, 'dopeiptv/assets/flags') for f in glob.glob('dopeiptv/assets/flags/*')]
datas += [(f, 'dopeiptv/assets') for f in glob.glob('dopeiptv/assets/*.svg')]
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
