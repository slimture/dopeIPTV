# Bug report: libmpv OpenGL render API — `INVALID_ENUM` / black hardware‑decoded video with subtitles (nvidia‑open)

Ready to file upstream (mpv: <https://github.com/mpv-player/mpv/issues>).
Consider cross‑posting to the NVIDIA open kernel modules tracker
(<https://github.com/NVIDIA/open-gpu-kernel-modules>).

## Summary

Using the **libmpv OpenGL render API** (`vo=libmpv`, `MPV_RENDER_API_TYPE_OPENGL`)
embedded in a Qt `QOpenGLWidget`, **hardware‑decoded 10‑bit HEVC video turns to
artifacts / black the moment a subtitle is composited**, while the subtitle
itself keeps rendering. The mpv log shows repeated:

```
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.
```

for both the video plane texture and the OSD (subtitle) texture.

- Standalone `mpv` (which uses `vo=gpu-next` / Vulkan) plays the **same file with
  the same subtitle and the same hardware decoding perfectly.**
- `hwdec=no` (software decode) through the render API is fine.
- Every hardware backend fails identically: `auto-copy-safe` (→ `vulkan-copy`),
  `nvdec`, `nvdec-copy`, `auto-safe`.
- Forcing a **desktop OpenGL 4.6 Compatibility** context (instead of the GLES
  3.2 context Qt hands out by default on Wayland) does **not** fix it — same
  `INVALID_ENUM`.
- Reproduces at 1080p as well as 4K (both 10‑bit HEVC).

So this is independent of the application: it reproduces in a ~190‑line harness
with only PyQt6 + libmpv (see `tools/hwdec_sub_test.py` in this repo).

## Environment

- GPU: NVIDIA GeForce RTX 4070 SUPER
- Driver: `nvidia-open` **610.43.03** (Arch Linux default open kernel modules)
- mpv: **0.41.0**
- Session: **Wayland**
- Qt: PyQt6 (`QOpenGLWidget`, libmpv render API)
- Content: HEVC Main 10, `p010`, `bt.2020/pq` (HDR), SubRip subtitles

## Key log excerpt (GLES default context)

```
libmpv_render: GL_VERSION='OpenGL ES 3.2 NVIDIA 610.43.03'
libmpv_render: Detected GLES 3.2.
libmpv_render: Loaded extension GL_EXT_texture_norm16.
...
Using hardware decoding (vulkan-copy).
vo/libmpv: reconfig to 3840x2160 p010 bt.2020-ncl/bt.2020/pq/limited/display
libmpv_render: Texture for plane 0: 3840x2160
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.   <-- video
...
libmpv_render: Reallocating OSD texture to 256x128.
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.   <-- subtitle
```

## Same failure with a forced desktop GL 4.6 context + nvdec‑copy

```
libmpv_render: GL_VERSION='4.6.0 NVIDIA 610.43.03'
libmpv_render: Detected desktop OpenGL 4.6.
Using hardware decoding (nvdec-copy).
libmpv_render: Texture for plane 0: 3840x2160
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.
libmpv_render: Reallocating OSD texture to 256x128.
libmpv_render: after creating texture: OpenGL error INVALID_ENUM.
```

Note: `INVALID_ENUM` is also logged with `hwdec=no`, where playback works — so
the error alone is not fatal, but with hardware decoding the video plane texture
is affected and the picture is lost.

## Reproduction

`tools/hwdec_sub_test.py` in this repository builds the libmpv render API on a
bare `QOpenGLWidget`, plays a file, enables the first full subtitle after 4 s,
and captures mpv's verbose log:

```
python3 tools/hwdec_sub_test.py FILE --minimal --sid 3 --seek 600 --verbose 2>mpv.log
python3 tools/hwdec_sub_test.py FILE --minimal --sid 3 --seek 600 --gl           # desktop GL
python3 tools/hwdec_sub_test.py FILE --minimal --sid 3 --seek 600 --hwdec no      # works
```

## Expected

Hardware‑decoded video composited with a subtitle through the OpenGL render API
renders correctly (as it does with `vo=gpu-next`), or the render API selects a
texture format the driver accepts.

## Workaround in this app

A "software decode when subtitles are shown" toggle: keep hardware decoding
everywhere, drop to `hwdec=no` only while a subtitle is active.
