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

from PyInstaller.utils.hooks import collect_all, collect_data_files


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
    result = [(staged, ".")]
    if sys.platform != "darwin":
        result += _libmpv_dep_binaries(real)
    return result


def _libmpv_dep_binaries(libmpv_path):
    """Bundle the shared libraries libmpv itself needs (Linux).

    python-mpv dlopen()s libmpv at runtime, so PyInstaller never analyses it
    and never pulls in libmpv's own dependencies - ffmpeg's libav*, libass,
    libplacebo and a long tail of codec libraries. They happen to be present
    on the build host and on a full desktop, so it "works on my machine", but
    a leaner box - or simply a newer Ubuntu whose ffmpeg soname differs from
    the one our libmpv was built against (24.04's libav*.so.60 vs 22.04's
    libav*.so.58) - can't load libmpv at all ("Failed to load dynlib") and the
    embedded player is dead. Walk ldd and bundle everything that isn't a core
    system / GL / X / Wayland / audio library (those must come from the host
    to match its kernel, driver, display server and sound server), so the
    player is genuinely self-contained on any machine."""
    import subprocess
    deny = (
        # C runtime + loader - must be the host's
        "linux-vdso", "ld-linux", "libc.so", "libm.so", "libdl.so",
        "libpthread.so", "librt.so", "libresolv.so", "libutil.so",
        "libgcc_s.so", "libstdc++.so", "libmvec.so", "libanl.so",
        # GL / Mesa / DRM / Vulkan - host's, matches its GPU driver
        "libGL", "libGLX", "libEGL", "libGLdispatch", "libOpenGL",
        "libglapi", "libgbm", "libdrm", "libgallium", "libLLVM", "libvulkan",
        # X / input - host's, matches its display server. NOTE: we do NOT
        # exclude libwayland-* here: libmpv's helper libs (libdecor, SDL2)
        # pull in libwayland-cursor/server, which aren't present on every
        # system (e.g. X11-only or a lean box), so bundle them or libmpv
        # fails to load. libwayland has a stable ABI and vo=libmpv never
        # opens its own Wayland connection, so a bundled copy is safe.
        "libX11", "libxcb", "libXext", "libXfixes", "libXrandr", "libXi",
        "libXrender", "libXau", "libXdmcp", "libxkbcommon",
        "libxshmfence",
        # audio - host's, matches its sound server
        "libpulse", "libasound", "libpipewire", "libjack",
        # font stack + ubiquitous base libs present on every desktop
        "libfontconfig", "libfreetype", "libz.so", "libzstd", "liblzma",
        "libbz2", "libudev", "libsystemd", "libdbus", "libcap", "libffi",
        "libpcre", "libselinux", "libmount", "libblkid",
    )
    try:
        out = subprocess.check_output(["ldd", libmpv_path], text=True)
    except Exception as e:
        print(f"WARNING: ldd on libmpv failed ({e}); not bundling its deps. "
              "The embedded player may fail on machines missing ffmpeg/libass.")
        return []
    stage_dir = tempfile.mkdtemp(prefix="dopeiptv-mpvdeps-")
    deps = []
    seen = set()
    for line in out.splitlines():
        if "=>" not in line:
            continue
        path = line.split("=>", 1)[1].strip().split(" ", 1)[0]
        if not path or not os.path.exists(path):
            continue
        base = os.path.basename(path)          # the soname, e.g. libavcodec.so.58
        if base in seen or any(base.startswith(p) for p in deny):
            continue
        seen.add(base)
        # Bundle under the SONAME libmpv actually references (DT_NEEDED), not
        # the fully-versioned real filename. ldd resolves "libavcodec.so.58" to
        # the file "libavcodec.so.58.134.100"; if we bundle it under the latter
        # name, libmpv's runtime lookup for "libavcodec.so.58" misses it and
        # libmpv fails to load ("Failed to load dynlib"). Stage a copy named
        # after the soname so the lookup resolves.
        staged = os.path.join(stage_dir, base)
        shutil.copy2(os.path.realpath(path), staged)
        deps.append((staged, "."))
    if deps:
        print(f"Bundling {len(deps)} libmpv dependencies: "
              + ", ".join(sorted(os.path.basename(s) for s, _ in deps)))
    return deps


binaries = _find_libmpv() + _find_ffmpeg()
datas = []
# Our own package data: the add-on locale JSONs (i18n loads them at import).
datas += collect_data_files('dopeiptv')
# Belt-and-suspenders: add the locale JSONs explicitly too, so the languages
# ship even if collect_data_files misses them. Without these the picker
# silently collapses to English-only in the frozen app.
datas += [(f, 'dopeiptv/locale') for f in glob.glob('dopeiptv/locale/*.json')]
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
# NOTE: we deliberately ship PyInstaller's full bundled OpenGL/Mesa stack
# (libGL/libEGL/libglapi/libgallium/libLLVM, i.e. the self-contained llvmpipe
# software renderer). An earlier version stripped it to use the host's GL, but
# that mixed a host libGL with our bundled libEGL and stopped the embedded
# player's window from coming up on some systems (e.g. an Ubuntu Wayland VM).
# The bundled stack is self-contained and is what works across machines,
# including software-only GL in VMs; the "did not find extension DRI_Mesa"
# lines it prints when it probes for a hardware driver are harmless noise.
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
