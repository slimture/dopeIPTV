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

Five themes (Midnight, Charcoal, Graphite, Slate, Nord) and seven accent
colors.  The active theme populates a global palette dict `P` and accent
string `ACCENT`.  `build_style()` generates the full QSS from these.
Theme changes take effect immediately without restarting playback.

## Testing

```bash
pip install pytest
pytest tests/
```

The test suite contains import smoke tests and unit tests for pure
utility functions.  GUI tests are not included (they would require a
display server).
