# Bug report — libmpv OpenGL render API: `INVALID_ENUM` / black hardware-decoded video with subtitles (nvidia-open)

File it on the mpv **Linux bug form**:
<https://github.com/mpv-player/mpv/issues/new?template=2_bug_report_linux.yml>

The sections below are laid out to match that form's fields one-to-one — copy
each block into the field with the same name. Fields marked _(fill in)_ need a
value from your own machine (commands are given). Consider cross-posting to the
NVIDIA open kernel modules tracker
(<https://github.com/NVIDIA/open-gpu-kernel-modules>) only if mpv triages this as
a driver bug.

> **Heads-up before filing:** this is a **libmpv render API**
> (`MPV_RENDER_API_TYPE_OPENGL`) issue and it does **not** reproduce in the
> standalone `mpv` player — that is itself the key data point, not a gap in the
> report. The form assumes standalone reproduction with `--no-config` /
> `--log-file`; where that does not apply, the render-API harness and its log
> stand in. The failing `glGenTextures`/`glTexImage` path is mpv's own render
> code, so mpv is the right first stop.

---

## mpv Information _(fill in — paste real `mpv --version`)_

```
# output of: mpv --version
mpv 0.41.0
# (paste your full mpv --version here, including libplacebo and FFmpeg lines)
```

libmpv is used through python-mpv (`python-mpv` 1.0.8) via the OpenGL render API,
not the CLI player.

## Other Information _(required — a few values to fill in)_

- **Distribution:** Arch Linux (`cat /etc/os-release | grep '^NAME'`)
- **Kernel:** _(fill in — `uname -a`)_
- **GPU:** NVIDIA GeForce RTX 4070 SUPER (`lspci -nn | grep -i vga`)
- **Driver:** `nvidia-open` **610.43.03** — Arch's default open kernel modules
  (`pacman -Q nvidia-open`)
- **GL/Vulkan driver strings:** _(fill in — `glxinfo -B` and `vulkaninfo | head`;
  the render API reports `OpenGL ES 3.2 NVIDIA 610.43.03` in the Qt context)_
- **Session / compositor:** Wayland, _(fill in compositor — GNOME/KDE/sway…)_
- **mpv binary source:** Arch repo `extra/mpv 1:0.41.0-3`; the failure is via
  **libmpv** (render API), embedded in a Qt `QOpenGLWidget`.
- **Last known-working version:** _(fill in if known — the app author recalls
  hardware-decoded subtitles working on an earlier driver; the exact
  nvidia/mpv combo was not pinned)_
- **When it started:** the picture is lost the instant a subtitle is composited
  onto hardware-decoded video; software decode (`hwdec=no`) is unaffected.

## Reproduction Steps _(required)_

This reproduces only through the **libmpv OpenGL render API** rendering into an
externally-supplied GL context (Qt `QOpenGLWidget`), so a `--no-config`
standalone run does **not** apply. A ~190-line PyQt6 + libmpv harness is the
minimal case (attached under *Sample Files*):

```
# reproduces (hardware decode + a subtitle -> artifacts/black, subtitle still draws):
python3 hwdec_sub_test.py FILE --minimal --sid 3 --seek 600 --gpu-debug 2>gpu-debug.log

# forcing a desktop OpenGL 4.6 context instead of GLES -> STILL fails:
python3 hwdec_sub_test.py FILE --minimal --sid 3 --seek 600 --gl

# software decode -> works:
python3 hwdec_sub_test.py FILE --minimal --sid 3 --seek 600 --hwdec no
```

The harness builds `MpvRenderContext(mpv, "opengl", ...)` on a bare
`QOpenGLWidget`, plays the file, and enables the first non-forced subtitle. FILE
is a 10-bit HEVC (HDR, `p010`, `bt.2020/pq`) with an embedded SubRip track.

**Standalone mpv does NOT reproduce** — same file, subtitle and hwdec render
correctly with **both `--vo=gpu` and `--vo=gpu-next`**:

```
mpv --no-config --vo=gpu      --hwdec=auto-copy-safe --sid=3 --start=600 FILE   # OK
mpv --no-config --vo=gpu-next --hwdec=auto-copy-safe --sid=3 --start=600 FILE   # OK
```

Every hardware backend fails identically through the render API:
`auto-copy-safe` (→ `vulkan-copy`), `nvdec`, `nvdec-copy`, `auto-safe`.
Reproduces at 1080p as well as 4K.

## Expected Behavior _(required)_

Hardware-decoded video composited with a subtitle through the OpenGL render API
renders correctly (as it does under `--vo=gpu`/`--vo=gpu-next`), or the render
API selects a texture format the driver accepts.

## Actual Behavior _(required)_

The hardware-decoded video plane turns to artifacts / black the moment a subtitle
is composited, while the subtitle itself keeps drawing. mpv logs `INVALID_ENUM`
on texture creation for both the video plane and the OSD (subtitle) texture.

GLES context (Qt's default on Wayland/NVIDIA):

```
libmpv_render: GL_VERSION='OpenGL ES 3.2 NVIDIA 610.43.03'
libmpv_render: Detected GLES 3.2.
Using hardware decoding (vulkan-copy).
vo/libmpv: reconfig to 3840x2160 p010 bt.2020-ncl/bt.2020/pq/limited/display
libmpv_render: Texture for plane 0: 3840x2160
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.   <-- video
libmpv_render: Reallocating OSD texture to 256x128.
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.   <-- subtitle
```

Forced desktop OpenGL 4.6 context + `nvdec-copy` — same failure:

```
libmpv_render: GL_VERSION='4.6.0 NVIDIA 610.43.03'
libmpv_render: Detected desktop OpenGL 4.6.
Using hardware decoding (nvdec-copy).
libmpv_render: Texture for plane 0: 3840x2160
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.
libmpv_render: Reallocating OSD texture to 256x128.
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.
```

With `--gpu-debug` the error brackets video rendering / texture creation itself:

```
libmpv_render: after video rendering: OpenGL error INVALID_ENUM.
libmpv_render: before video texture creation: OpenGL error INVALID_ENUM.
```

Note: `INVALID_ENUM` is also logged with `hwdec=no`, where playback works — so
the error alone is not fatal, but with hardware decoding the video plane texture
is affected and the picture is lost.

## Log File _(required — attach the file)_

Attach `gpu-debug.log` produced by the reproduction run above
(`--gpu-debug`, mpv `loglevel=debug`, which is the render-API equivalent of
`--gpu-debug --log-file=output.txt` for the embedded case). The log was captured
while the issue was actively occurring, and is the full, untruncated file. There
is no crash, so no backtrace applies.

## Sample Files _(optional)_

- The reproduction harness: `hwdec_sub_test.py` (~190 lines, PyQt6 + libmpv only)
  — in the dopeIPTV repo at `tools/hwdec_sub_test.py`
  (<https://github.com/slimture/dopeIPTV/blob/main/tools/hwdec_sub_test.py>).
- A short 10-bit HEVC/HDR clip with an embedded SubRip subtitle reproduces it;
  archive as `.zip` and host on 0x0.st / Google Drive if attaching.

---

## Workaround in the affected app (for reference)

Software decoding (`hwdec=no`) is the default and sidesteps the render path
entirely. Hardware decoding is an opt-in; when enabled, `hwdec-software-fallback`
is set but only catches decoder failures, not this render-path `INVALID_ENUM`
(the decoder succeeds here). An earlier "software decode only while a subtitle is
shown" toggle was removed: switching `hwdec` live on the running stream to
recover left the pipeline corrupted on the next file, so a fixed decode mode per
stream is the only reliable behaviour on this driver stack.
