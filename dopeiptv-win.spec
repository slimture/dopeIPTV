# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for dopeIPTV on Windows (onedir bundle).

python-mpv loads libmpv (``mpv-2.dll``) through ctypes at runtime, so
PyInstaller's import analysis never sees it - we locate the DLL ourselves and
put it next to the exe so ``CDLL("mpv-2.dll")`` (and ``platform_windows.
find_libmpv``) resolve it. The Windows libmpv build (e.g. shinchiro's
``mpv-dev``) statically bundles ffmpeg, so no separate av* libraries are needed.
"""

import glob
import os
import shutil
import tempfile

from PyInstaller.utils.hooks import collect_all, collect_data_files


def _find_libmpv_win():
    """Bundle mpv-2.dll (or libmpv-2.dll) next to the exe.

    Search order: $LIBMPV_DIR, a ./libmpv dir, then PATH. CI drops the DLL from
    the mpv-dev archive into ./libmpv before building.
    """
    names = ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll")
    dirs = [os.environ.get("LIBMPV_DIR", ""), os.path.join(os.getcwd(), "libmpv")]
    dirs += os.environ.get("PATH", "").split(os.pathsep)
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                # Stage under the canonical name python-mpv dlopen()s.
                staged = os.path.join(tempfile.mkdtemp(prefix="dope-mpv-"), "mpv-2.dll")
                shutil.copy2(p, staged)
                print(f"Bundling libmpv: {p} -> mpv-2.dll")
                extra = [(staged, ".")]
                # Ship any sibling DLLs the build split out (rare; mpv-dev is
                # usually self-contained).
                skip = {n.lower() for n in names}
                for dep in glob.glob(os.path.join(d, "*.dll")):
                    if os.path.basename(dep).lower() not in skip:
                        extra.append((dep, "."))
                return extra
    print("WARNING: mpv-2.dll not found; the embedded player will not work in "
          "this build. Put mpv-2.dll in ./libmpv or set LIBMPV_DIR.")
    return []


def _find_ffmpeg_win():
    exe = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if exe:
        return [(exe, ".")]
    print("NOTE: ffmpeg.exe not found; recording falls back to mpv stream-record.")
    return []


binaries = _find_libmpv_win() + _find_ffmpeg_win()
datas = []
# Our own package data: the add-on locale JSONs (i18n loads them at import).
datas += collect_data_files('dopeiptv')
# Belt-and-suspenders: add the locale JSONs explicitly too, so the languages
# ship even if collect_data_files misses them. Without these the picker
# silently collapses to English-only in the frozen app.
datas += [(f, 'dopeiptv/locale') for f in glob.glob('dopeiptv/locale/*.json')]
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

if os.path.exists(os.path.join("dopeiptv", "_tmdb_key.py")):
    hiddenimports += ["dopeiptv._tmdb_key"]

_icon = os.path.join("packaging", "dopeIPTV.ico")
icon = _icon if os.path.exists(_icon) else None


def _version_file():
    """Write a Windows VERSIONINFO resource and return its path.

    An .exe with NO version resource - no company, no product, no
    description - is one of the things Defender's heuristics weigh, and a
    PyInstaller binary already starts from a bad reputation. Every real
    Windows program carries this block; ours carried nothing.

    It is not a cure on its own (see WINDOWS-README.txt: the actual fix is
    an authenticode signature), but it is free and it is the one part of
    the problem that lives in the build rather than in a certificate."""
    import re
    src = open(os.path.join("dopeiptv", "__init__.py"), encoding="utf-8").read()
    m = re.search(r'^__version__ = "([^"]+)"', src, re.M)
    ver = m.group(1) if m else "0.0.0"
    parts = [int(x) for x in re.findall(r"\d+", ver)][:4]
    parts += [0] * (4 - len(parts))
    quad = ", ".join(str(p) for p in parts)
    body = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}), prodvers=({quad}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'dopeIPTV'),
      StringStruct('FileDescription', 'dopeIPTV - IPTV player'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'dopeiptv'),
      StringStruct('LegalCopyright', 'GPL-3.0-or-later'),
      StringStruct('OriginalFilename', 'dopeiptv.exe'),
      StringStruct('ProductName', 'dopeIPTV'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = os.path.join(tempfile.mkdtemp(prefix="dope-ver-"), "version_info.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    print(f"Windows version resource: {ver}")
    return path


version_file = _version_file()


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
    upx=False,
    # GUI app: no console window. (During bring-up this was True to surface
    # Qt/mpv/GL errors; playback is stable now, so hide the terminal.)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    version=version_file,
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
