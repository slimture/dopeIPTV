# Porting dopeIPTV to Windows — scope & action list

> **Shipped in 0.7.2.** A portable Windows x64 build (`dopeIPTV-*-windows-x64.zip`)
> is built by CI and attached to every release. The notes below are kept as the
> historical porting plan / design record.

## Status

**Scaffolding has landed** (items A, C, E, F, H, I below):
- `core/platform_windows.py` — libmpv discovery, desktop-OpenGL setup, `WakeLockWindows`.
- Wired into `app.py` (OpenGL on win32), `media/players.py` (libmpv soname + discovery), `core/wakelock.py` (native lock).
- `dopeiptv-win.spec` (PyInstaller onedir, bundles `mpv-2.dll`, `.ico`).
- CI: a `windows-zip` job in `release.yml` (fetches libmpv, builds a portable zip, attaches it to the release). `continue-on-error` while it settles.
- Website recognises the Windows `.zip` (sync `USER_EXTS` + `classify`), hero OS-detect updated.

**Remaining before it's usable:**
1. **Spike item B** — run `tools/hwdec_sub_test.py` on a real Windows box (Intel/AMD/NVIDIA) to confirm the libmpv render API paints. This is the go/no-go.
2. **First CI run** — the libmpv-fetch step (zhongfly `mpv-dev-x86_64` archive) likely needs one tweak against the real asset names; `continue-on-error` keeps it from blocking a release meanwhile.
3. Optional polish: item D (player paths), an installer instead of a zip.

---

**Verdict:** feasible, **moderate effort (~2–4 focused days)**, not a rewrite.
PyQt6 + libmpv (render API) is a well-trodden cross-platform stack, and the code
is already platform-abstracted (a `platform_macos` module, a `WakeLock`
inhibitor, `player_exec` executable discovery, and the Linux-only `force_x11`
setting already guarded behind `sys.platform`). Windows is mostly "fill in the
gaps" plus packaging.

The one real technical risk to retire early is the **OpenGL context** the libmpv
render API gets on Windows (see item B) — spike that first on a real Windows box
before committing to the rest.

---

## Already cross-platform (no work)

- **PyQt6/Qt**, **python-mpv**, **requests**, **PyChromecast/zeroconf** — all run on Windows.
- **`force_x11` / XWayland / Wayland** paths are Linux-only and already guarded
  (`mw_settings.py` `sys.platform.startswith("linux")`, `app.py` likewise) — they
  simply won't appear or run on Windows.
- **`os._exit(0)`** at teardown works on Windows.
- **Per-OS keyboard shortcuts** already keyed by `sys.platform`.
- The **nvidia-open INVALID_ENUM** hazard is Linux-specific — irrelevant on Windows.

---

## Work items (ordered)

### A. Bundle & locate libmpv — the critical item
python-mpv loads libmpv via `ctypes.util.find_library("mpv")`. On Windows that
resolves `mpv-2.dll` / `libmpv-2.dll` from the DLL search path.

- Add **`core/platform_windows.py`** with a `find_libmpv()` that registers the
  bundled DLL directory (mirrors `platform_macos.find_libmpv`):
  ```python
  import os, sys
  def find_libmpv() -> None:
      # DLL ships next to the frozen exe (or in a libmpv/ subdir in dev).
      base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                             else os.path.abspath(__file__))
      for d in (base, os.path.join(base, "libmpv")):
          if os.path.isfile(os.path.join(d, "mpv-2.dll")):
              os.add_dll_directory(d)          # Python 3.8+
              os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
              return
  ```
- Call it from `app.py` on `sys.platform == "win32"` (next to the existing
  `darwin` branch), **before** the first mpv import.
- **Vendor `mpv-2.dll`** into the build. Source: shinchiro's libmpv dev builds
  (`mpv-dev-x86_64-*.7z` → contains `libmpv-2.dll`, rename to `mpv-2.dll`).

### B. OpenGL context (validate first)
Qt6 on Windows may hand mpv either desktop OpenGL (`opengl32`) or ANGLE
(GLES-over-D3D). The render API needs a working GL context.

- Add a `win32` branch in `app.py` that requests **desktop OpenGL** and a sane
  format (analogous to the macOS `setup_opengl()` which asks for 4.1 Core):
  ```python
  QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
  fmt = QSurfaceFormat(); fmt.setVersion(3, 3)
  fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
  QSurfaceFormat.setDefaultFormat(fmt)
  # optionally AA_UseDesktopOpenGL
  ```
- **Spike:** run the existing `tools/hwdec_sub_test.py` (already cross-platform,
  PyQt6 + libmpv only) on Windows with Intel/AMD/NVIDIA GPUs to confirm the
  render API paints. This is the single biggest unknown; if it paints, the rest
  is mechanical.

### C. WakeLock (screen-sleep inhibit)
`WakeLock` has macOS (caffeinate) and Linux (D-Bus) paths; on Windows it
currently no-ops (falls through the D-Bus path, which fails silently). Non-fatal
— the app runs, the screen may just sleep during playback.

- Add a Windows branch using `SetThreadExecutionState`:
  ```python
  import ctypes
  ES_CONTINUOUS=0x80000000; ES_SYSTEM_REQUIRED=0x1; ES_DISPLAY_REQUIRED=0x2
  # acquire:
  ctypes.windll.kernel32.SetThreadExecutionState(
      ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
  # release:
  ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
  ```

### D. External-player discovery (minor)
`player_exec.find_player_executable` uses `shutil.which`, which already resolves
`mpv.exe` via `PATHEXT`. External players are optional (the embedded player is
the default), so this is low priority. Optionally add Windows candidate paths
(`%ProgramFiles%\mpv\mpv.exe`, scoop/choco shims).

### E. PyInstaller spec — `dopeiptv-win.spec`
Model it on `dopeiptv.spec`. Must:
- add `mpv-2.dll` (and its deps if any) to `binaries`,
- include the same `datas` (icons, etc.),
- set `console=False` (windowed), an `.ico` icon, and one-dir output.
- Windows needs a **`.ico`** — generate from the existing PNG (item H).

### F. CI — add a Windows job to `.github/workflows/release.yml`
New `windows-build` job on `windows-latest`:
1. `actions/setup-python`,
2. `pip install PyQt6 requests python-mpv pychromecast pyinstaller`,
3. download + extract libmpv dev, drop `mpv-2.dll` next to the spec,
4. `pyinstaller --noconfirm dopeiptv-win.spec`,
5. package the `dist/dopeiptv/` folder as an **installer `.exe`** (Inno Setup or
   NSIS) — nicer than a zip and, importantly, the website's release sync already
   recognises `.exe`/`.msi` (`USER_EXTS`), so a `.exe` asset appears on
   iptv.dope.rs automatically. (If you'd rather ship a portable `.zip`, add
   `.zip` to `USER_EXTS` in `website/sync-releases.php`.)
6. `actions/upload-artifact` + attach to the release like the other jobs.

### G. Platform-guard audit (quick pass)
- Confirm no hardcoded POSIX runtime paths (`/tmp`, `~/.config`, `/usr`) — config
  goes through `QSettings`, which is portable. (Spot-check `stores`, `recording`,
  `epg` cache dirs use `QStandardPaths`.)
- Verify the PiP frameless/always-on-top flags (the non-`darwin` branch) behave
  on Windows — Qt window flags are portable; likely fine, just test.

### H. Icon
Produce `packaging/dopeIPTV.ico` (multi-size) from the existing 256×256 PNG for
the exe and installer.

### I. Website (after the build exists)
- The download card appears **automatically** once a `.exe` is in the release
  (`classify()` already maps `.exe`→🪟 Windows).
- Update the hero OS-detect in `website/public/app.js`: it currently labels
  Windows "build from source" — point it at the download once the build ships.

---

## Suggested order of execution
1. **Spike item B** (render API on Windows) with `tools/hwdec_sub_test.py`. Go/no-go.
2. Items A + E + H (bundle libmpv, spec, icon) → a working local `.exe`.
3. Item F (CI) → automatic Windows release assets → auto-appears on the site.
4. Items C, D, G, I (polish).

## Effort
~2–4 focused days once a Windows test machine (or a Windows CI runner for
iteration) is available. The long pole is validating the GL context (B); the
rest is packaging and small platform branches that follow the existing
`platform_macos` pattern.
