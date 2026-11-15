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
  reminders, the system's media controls - MPRIS, Now Playing, the SMTC -,
  notifications, the popular-titles chart)
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

## Channel stream information

Playback's "Show stream information" switch (off by default) controls a
four-second banner after a live channel loads and the technical line below
its EPG progress bar in list view. `media/stream_info.py` reads the
selected video track from the existing mpv core once at `file-loaded`,
checks the loaded path, and reads it again only when mpv reports a format
change through an observer on `video-params`. Both arrive on mpv's event
thread and reach the GUI thread through signals; nothing polls. FPS is the
source's `demux-fps`, not the display or deinterlacer's output rate.
Missing fields stay absent; nothing is inferred from a channel's name.

`core/stream_info.py` validates the metadata and keeps at most 500 channel
entries in QSettings, keyed by source and channel; `ui/mw_stream_info.py`
is the window's side. Non-playing rows show the last observed values, not
a fresh measurement. No probe or extra provider connection is opened.
The cache lists the channels someone watched, so private viewing neither
reads nor updates it, Clear recent history drops it whenever channels are
cleared (it keeps no times, so any range takes all of it), backups leave
it out and sync never carried it. Disabling the switch immediately hides
both views.

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

## A source's own network (Nerd: own DNS, IP version, pinned certificate)

All three are a `Route` (`core/net_route.py`), inactive - and changing
nothing - while Nerd is off or nothing is set.

- **The app's own requests** (API, guide, logos, the rewind buffer's
  connection) go through `core/net_adapter.py`: a requests adapter whose
  urllib3 connections resolve the host as the route says and connect to that
  address, while Host, SNI and the certificate check stay the name's. A
  pinned certificate is checked by its SHA-256 on its host and port only
  (chain not asked, fingerprint compared after the handshake); verification
  is never switched off anywhere else. Through a proxy, the proxy resolves.
- **The player and recordings** (mpv, ffmpeg) go through
  `core/net_bridge.py`, a loopback proxy set as `http-proxy` /
  `-http_proxy`. Putting the IP in the address with a Host header would work
  for plain http only: ffmpeg takes the TLS server name from the address, so
  an https stream would send no SNI and a CDN would refuse it, and a
  redirect or a playlist segment on another host would get the wrong Host.
  Through a proxy, ffmpeg asks `CONNECT host:port` for https and does TLS
  itself (name intact); plain http comes as an absolute address, sent on
  with `Connection: close` so every request is resolved for its own host.
  mpv does not check certificates (`tls-verify` is off by default), so for a
  pinned source the bridge makes a TLS connection of its own before it opens
  a tunnel and refuses it when the fingerprint differs (rechecked every
  ten minutes).
- **Not covered**: rtmp / rtsp / udp streams and multiview cells use the
  system's resolver; names only the home network knows are never sent to a
  public DNS; a custom DoH server's own name is resolved by the system (the
  presets are addressed by IP).

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
loads the bundled libmpv, and signs the list of the bundle's own files
(`tools/build_manifest.py` through the bundle's `--build-files`): what tells
an official build from a modified one (`core/build_check.py`,
docs/MAINTAINING.md "Official builds").

## AirPlay to Apple TV (macOS)

AirPlay goes through Apple's own frameworks, so it is a macOS feature, and
only the way Apple documents them - no private API, nothing reached inside
Apple's views. `services/airplay_macos.py` makes an `AVPlayer` with
`allowsExternalPlayback` and, after Cast, opens a small "AirPlay" window of
its own (an `NSPanel`): an `AVPlayerLayer`, the player's video output, and
AVKit's `AVRoutePickerView` with `player` set (macOS 10.15+), which is
Apple's documented control for routing one AVPlayer's video. The user
clicks Apple's AirPlay button there and picks the Apple TV; the app never
clicks it. `AVPlayerView` was not used: its own play button and timeline
would sit beside the cast strip, and its play plays on the Mac. The panel
is made once per cast (a fallback or a seek keeps it), floats over the
cast dialog and works while it is modal (`worksWhenModal`; Qt runs a modal
dialog as an AppKit modal session). It is never an NSView inside a Qt
widget: the first version put the picker into the dialog's row, and on a
real Mac choosing the device flickered back and forth - Qt and AppKit
painting over each other, each list refresh making the button again, and
the app opening Apple's menu by clicking the button inside it. The
dialog's row is plain Qt: what to do next, and "Show the AirPlay window".
Closing the panel before an Apple TV is picked ends the cast; after, it
goes on.

The player never plays on the Mac: paused and muted from the start, it
plays only once `externalPlaybackActive` has held for a second, is muted
the moment it goes off and paused once that has held as long - so a route
that blinks during the hand-over neither starts nor stops anything (the
cast ends 4 s after the route is gone, or two minutes with nothing picked).
The dialog lists "AirPlay (Apple TV)" when `AVRouteDetector` (on only while
the dialog is open - Apple says it costs power) finds routes, and also when
it gives no answer; the scan's worker waits up to 3 s for it, and routes
found later add the entry. Everything on AVFoundation and AppKit runs on
the GUI thread: the cast's worker threads queue onto it; key-value
observing (`externalPlaybackActive`, `timeControlStatus`, the item's
`status`) and the notifications (end, failure, stall, error-log entry, the
route detector, the panel closing) and the picker's delegate only queue a
kick to it; a 1 s tick reads the position and keeps the deadlines, and
reads the route too should observing be unavailable. Each step is in the
log ("airplay: ..."): the window opened, Apple's menu opened and closed,
`externalPlaybackActive`, the stream's status, a failure as NSError domain
and code (with the error under it), each AVFoundation error-log entry - no
address with its login (`redact_url`) and not the bridge's token. The
receiver joins `CastHub` through `add_receiver` (`ui/mw_airplay.py`), so
the strip drives it like any cast. `NSBonjourServices` (`_airplay._tcp`,
`_raop._tcp`), `NSLocalNetworkUsageDescription` and App Transport
Security's media and local-network exceptions are in the Mac app's
Info.plist (`dopeiptv-mac.spec`).

Verified only on a real Mac with an Apple TV (CI has stand-ins for PyObjC,
so none of this is proven there): that the panel and Apple's button show
and take clicks over the modal cast dialog; that picking the Apple TV turns
`externalPlaybackActive` on while the player is still paused, and that the
picture - not only the sound - goes over with the layer attached; that the
picture keeps going with the panel closed or hidden (the layer stays
attached to the player); that KVO, the picker's delegate and
`AVRouteDetector` fire as described; macOS 15's local-network prompt; and
the firewall's question.

An Apple TV plays HLS and MP4, not a running MPEG-TS, Matroska or MP2
sound. `providers/airplay_bridge.py` decides (`plan`): what is already
HLS or MP4 with codecs it takes goes as it is (an Xtream channel's .m3u8,
an MP4 film), and falls back to the bridge once if the TV refuses it;
anything else - or a source that sends its own user agent, headers or
proxy (Nerd), which the TV would not - is repackaged by the bundled ffmpeg
as HLS with fragmented-MP4 segments (2 s; a channel's playlist rolls over
five minutes, a film keeps every segment and is served as VOD once ffmpeg
ends it), on a small HTTP server on the LAN address under a random token,
removed when the cast stops. H.264/HEVC (tagged `hvc1`) and AAC/AC-3/E-AC-3
are copied; other sound becomes AAC, MPEG-2 and other pictures H.264 (the
panel says that costs the processor). The codecs come from mpv when the
title is playing, else from `ffmpeg -i` (the Mac bundles no ffprobe). The
loopback rewind-buffer address never goes to the TV. A firewall that
blocks the app (`socketfilterfw`) refuses the cast with the way to allow
it; a TV that never reaches the bridge after 20 s is told the same.

Zapping while casting (any receiver: `ui/mw_cast.py`, `_cast_zap`): a live
channel picked during a cast goes to the same receiver, through the
playback path's checks, one at a time (the newest picked goes once the one
on its way is in). For AirPlay that is a new item in the same AVPlayer
(`replaceCurrentItemWithPlayerItem`): Apple's button, the window and the
picked Apple TV stay; with the bridge the old ffmpeg stops before the new
one asks the provider (`settle`), and a route that goes while the Apple TV
loads the new item does not end the cast (`SWAP_GRACE_S`, until the item
is ready). The strip has no volume for AirPlay - AVPlayer's has no effect
while the Apple TV plays the video, its remote sets it - and its menu
brings the AirPlay window back.

Why not elsewhere: pyatv was tried on real hardware in July 2026 (an Apple
TV 4K pairs and connects, `play_url` fails with HTTP 500 on
`/playback-info` even for Apple's own demo stream; tvOS 18 broke the legacy
AirPlay video API for third-party senders, [pyatv
#2512](https://github.com/postlund/pyatv/issues/2512)). Only Apple's own
stack still sends video, so Linux and Windows have no AirPlay entry.

