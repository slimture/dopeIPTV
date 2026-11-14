# Developer Notes

## Setup

```bash
pip install -r requirements.txt   # PyQt6, requests, python-mpv, pychromecast
sudo apt install mpv ffmpeg       # libmpv for the embedded player, ffmpeg for recording
python3 dopeiptv.py
```

Without libmpv the app still runs and opens channels in an external mpv
window; without ffmpeg, recording falls back to mpv.

## Package structure

The application was refactored from a single 6000-line `dopeiptv.py`
monolith into a proper Python package (`dopeiptv/`).  The top-level
`dopeiptv.py` is now a thin launcher that calls `dopeiptv.app.main()`.

The package is organised into layered subpackages with a one-way import
rule (see `ARCHITECTURE.md` for the full module map):

- `providers/` — data sources (Xtream / M3U / Stalker clients, EPG, TMDB,
  Trakt, OpenSubtitles, Chromecast and DLNA)
- `core/`      — storage, background workers, OS integration
- `media/`     — playback (embedded libmpv widget, external launchers)
- `services/`  — window-agnostic application services (cover art, resume,
  reminders, MPRIS, notifications, the popular-titles chart)
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

## Stalker / Ministra portals

`providers/stalker.py` (`StalkerClient`) talks to a MAC portal's
`portal.php` / `load.php`: a handshake for a token, `get_profile`, then the
`itv` / `vod` / `series` listings, and `create_link` for a playable URL.
It offers the same calls as `XtreamClient`, so the window does not know
which kind it has.  A portal hands out a link per play (`create_link`),
which is resolved off the GUI thread (`link_ready`, with a time limit) so
a slow portal cannot freeze the window.

A portal keeps its archive by programme.  A channel row flags it
(`tv_archive`, `archive` or `enable_tv_archive`; the depth,
`tv_archive_duration`, is in hours and becomes whole days for the
window), the guide table (`get_simple_data_table`, one day per call)
lists programmes with their ids, and `type=tv_archive&action=create_link`
with `cmd=auto /media/<programme id>.mpg` gives a link that plays that
programme from its start.  `StalkerClient.archive_link` finds the
programme for a moment (or the one a guide's programme overlaps most),
then asks create_link with the programme's own `cmd` if the guide gives
one, its id, then its `real_id`; `archive_by_programme` tells the window
to ask for it off its thread (`ui/mw_portal_archive.py`) instead of
`timeshift_urls`.  The player starts as far into the link as was asked
(`_start_playback(start_at=...)`), a go-back carries on with the next
programme when one ends, and multiview and casting leave a portal's
archive alone.  Tested against a fake portal only
(`tests/test_stalker_archive.py`): no real portal with an archive has
been tried yet.

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

## Rewind and dopeIPTV's own buffer (DVR)

Two ways to rewind a live channel:

1. **mpv's own cache** (the default): "Rewind live channels" sets how many
   minutes mpv's demuxer keeps, in memory or - with "Keep the rewind
   buffer on disk" - in a cache file (`core/local_buffer.py` maps the
   seekable window).  Pausing for long pauses the download too, so LIVE
   may have to reopen the channel.
2. **The app's own buffer** (experimental, `setting_dvr_kept`): the
   `core/dvr_*` modules.  `DvrIngest` keeps one connection to the provider
   and reconnects in runs, `DvrStore` writes rolling MPEG-TS segments under
   a space budget (oldest first, also when the disk runs low), `DvrServer`
   serves them to mpv on 127.0.0.1 behind a random token, `DvrSession` ties
   the three together for one channel, and `DvrPool` keeps every channel's
   frozen history for the session within one budget.  `BufferRecorder`
   (`dvr_record.py`) records from the store, so a recording neither needs a
   second connection nor stops while the player is paused.  The player side
   is `media/dvr_player.py`; the bar (DVR | TIMESHIFT) is
   `media/rewind_bar.py`.

## Home

`ui/mw_home.py` builds the page; `ui/home_widgets.py` draws it (featured
strip, shelves, cards - each card one composed, opaque pixmap, clicks
taken on release, which is what keeps it smooth and avoided a macOS
crash).  The popular shelves come from `services/popular.py`: TMDB's
weekly chart matched against the provider's catalogue.  They need a TMDB
key: release builds bundle one (`dopeiptv/_tmdb_key.py`, generated in CI
and git-ignored); a source checkout uses the user's own key from
Settings → Posters & info or `DOPEIPTV_TMDB_KEY`, and without any key those
shelves are simply left out.

## EPG caching

`XmltvGuide` caches parsed XMLTV data to disk per playlist with a 6-hour
TTL.  On startup it loads stale cache first (instant UI), then refreshes
in the background.  Cache files live in the platform's cache directory.

## Theme system

`THEMES` in `ui/theme.py` (25 today, five of them light) and `ACCENTS`
(ten, plus the theme's own and any custom colour).  The active theme
populates a global palette dict `P`; `build_style()` generates the full
QSS from it.  Theme changes take effect immediately without restarting
playback.  `tests/test_theme.py` checks that every theme has every key
and that text stays readable on it - add a theme there and the test
tells you what is missing.

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
