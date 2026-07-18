# Developer Notes

## Setup

```bash
pip install PyQt6 requests
sudo apt install mpv
python3 dopeiptv.py
```

Optional dependencies:
- `pip install python-mpv` - embedded in-app video player
- `pip install pychromecast` - Chromecast casting
- `sudo apt install ffmpeg` - standalone stream recording

## Package structure

The application was refactored from a single 6000-line `dopeiptv.py`
monolith into a proper Python package (`dopeiptv/`).  The top-level
`dopeiptv.py` is now a thin launcher that calls `dopeiptv.app.main()`.

The package is organised into layered subpackages with a one-way import
rule (see `ARCHITECTURE.md` for the full module map):

- `providers/` — data sources (Xtream client, EPG, TMDB, Trakt, cast)
- `core/`      — storage, background workers, OS integration
- `media/`     — playback (embedded libmpv widget, external launchers)
- `services/`  — window-agnostic application services (cover art, resume)
- `ui/`        — the main window, its mixins and all widgets/dialogs

`ui` may import from any layer below it; `services` builds on
`providers`/`core` but never imports `ui`; `core` depends on nothing
above it. Keeping this direction one-way is what lets you change a
module without breaking the layers beneath it.

## Xtream Codes API

The app connects to Xtream Codes providers via their HTTP API
(`player_api.php`).  Key endpoints used:

- `get_live_categories` / `get_live_streams` - live TV
- `get_vod_categories` / `get_vod_streams` / `get_vod_info` - movies
- `get_series_categories` / `get_series` / `get_series_info` - series
- `xmltv.php` - EPG data (XMLTV format)
- Stream URLs: `{server}/{username}/{password}/{stream_id}.ts`

## Embedded player

The embedded player uses libmpv's OpenGL render API via `python-mpv`.
The `_MpvGLWidget` subclasses `QOpenGLWidget` and feeds frames through
`MpvRenderContext`.  This is the only way to get tear-free video inside
a Qt widget.

Requirements: `python-mpv` pip package + `libmpv.so` / `libmpv.dylib`
system library.

## Recording

Two recording modes:

1. **Standalone** (`RecordingManager._spawn`): launches ffmpeg (or mpv)
   as a subprocess doing stream-copy. Supports scheduled start/stop
   times and file size caps.

2. **In-player** (`--stream-record`): records whatever the embedded
   player is currently showing.  Uses mpv's built-in stream recording.

## EPG caching

`XmltvGuide` caches parsed XMLTV data to disk per playlist with a 6-hour
TTL.  On startup it loads stale cache first (instant UI), then refreshes
in the background.  Cache files live in the platform's cache directory.

## Theme system

Nine themes (Graphite, Midnight, OLED, Nord, Dracula, Gruvbox, Solarized,
Catppuccin, Light) and seven accent colors.  The active theme populates a
global palette dict `P`.  `build_style()` generates the full QSS from
these.  Theme changes take effect immediately without restarting playback.

## Logging

All diagnostics go through one logger (`dopeiptv/core/log.py`), configured
once in `app.py`'s `main()`. Use it instead of `print()`:

```python
from ..core.log import log
log.info("…"); log.warning("…"); log.error("…"); log.debug("…")
```

- `DOPEIPTV_LOG=debug` turns on the verbose timeshift / image / TMDB traces
  (one switch; it replaced the old `DOPEIPTV_TS_DEBUG` / `DOPEIPTV_IMG_DEBUG`
  flags).
- `DOPEIPTV_LOG_FILE=/path/to.log` also tees everything to a small rotating
  file - the easiest thing to attach to a bug report.

## Quality gate (CI)

`.github/workflows/ci.yml` runs on every push and PR. Reproduce it locally
before committing:

```bash
pip install ruff mypy pytest
ruff check dopeiptv tests                    # lint: pyflakes + bugbear + C4
mypy                                         # types (scoped, see pyproject)
QT_QPA_PLATFORM=offscreen pytest -q          # the full suite, headless
```

The suite is import smoke tests + unit tests for pure logic (no GUI tests -
they'd need a display server). `mypy` type-checks the pure-logic modules
today (`[tool.mypy] files` in `pyproject.toml`); grow that list as modules
gain type hints. A release build also runs a GUI-less `--self-check` that
loads the bundled libmpv.

## Parked: AirPlay to Apple TV

Proven not viable on 2026-07-18 with real hardware: pyatv 0.18.0 pairs and
connects to an Apple TV 4K fine, but `play_url` fails (HTTP 500 on
`/playback-info`, nothing renders on the TV) — even with Apple's own public
HLS demo stream, so it is not a provider-format problem. Known upstream
issue ([pyatv #2512](https://github.com/postlund/pyatv/issues/2512), closed
unresolved): tvOS 18 effectively broke the legacy AirPlay video API for
third-party senders.

To re-evaluate after a pyatv release that mentions AirPlay-video/`play_url`
fixes, run `python3 tools/airplay_probe.py --demo` against an Apple TV
(2 minutes; pairing credentials are cached). If the demo stream plays, the
planned design is: an `AppleTVManager` mirroring `ChromecastManager` (lazy
import, its own asyncio loop in a worker thread), Apple TVs listed in the
shared cast dialog, a PIN pairing flow, `.m3u8` forced for live, and the
option hidden for `.mkv` VOD (AirPlay cannot play Matroska). macOS users
always have the OS-level fallback: Control Center screen mirroring.
