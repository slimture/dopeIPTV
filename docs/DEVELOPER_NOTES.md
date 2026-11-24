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

- `providers/` — data sources and the network edge (Xtream / M3U / Stalker
  clients, the boxes and TV servers at home, Jellyfin / Emby / Plex, EPG,
  TMDB, Trakt, Simkl, OpenSubtitles, radio-browser.info, and the cast
  targets: Chromecast, DLNA, Kodi, AirPlay's bridge)
- `core/`      — storage, background workers, OS integration, and the
  logic without Qt widgets (the rewind buffer, recordings, sync, the phone
  remote's and the remote API's servers, ...)
- `media/`     — playback (embedded libmpv widget, external launchers)
- `services/`  — window-agnostic application services (cover art, resume,
  reminders, the system's media controls - MPRIS, Now Playing, the SMTC -,
  notifications, the popular-titles chart, failover, the search across
  sources, the shared lists and server mode)
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

By default the picture is drawn on a thread of its own
(`media/video_thread.py`): each surface is a native child window (a
`QWindow` in `QWidget.createWindowContainer`) and a render thread owns the
GL context and mpv's render context. mpv's update callback only wakes that
thread; it renders straight into the window and swaps, so the waits for a
frame's display time and for vsync never land on the window's thread, and
the window has no GL widget left - a scroll repaints only what moved. Every
video frame through the window's thread had kept it busy 99% of the time
while video played (scrolling stuck at the video's frame rate).

The widgets over the picture (loading indicator, DVR row, stats, badges,
the fullscreen controls, multiview's labels) stay where they are: the
native window takes no input (`WindowTransparentForInput`), so they get
their clicks, hover and tooltips as before, and what they show is drawn
into the picture by mpv (`overlay-add`, BGRA premultiplied), again only
when one changes. Each is rendered into a transparent image with
`DrawChildren` alone - only what it paints itself, as over the old
surface (`DrawWindowBackground` filled its rectangle: a square box around
the round centre button) - and only once the layout waiting for it has
run (`_settle`), so mpv never gets a picture of it laid out the old way. While mpv has no picture the native window is hidden and
everything is plain Qt. Any of those widgets can be deleted at any moment
(a notice, a dialog, the pop-out's button): the layer never asks a deleted
one anything (`sip.isdeleted`; its `destroyed` signal takes it off the
picture at once), its event filter never raises, and it is uninstalled
from every widget when the surface falls back or goes. The render thread
lets go of a window before it goes - `release` on the window's
`SurfaceAboutToBeDestroyed` (QWindowContainer sends it before deleting
it), the pop-out's when it is dropped, the whole thread when its surface
is destroyed (`_Ending`) - and a window gone under it is no failure.

Menus over the video are windows of their own (popups, never drawn into
mpv). The right-click ones - docked, fullscreen, pop-out, a multiview
cell, the guide - open on the button's press and go through
`ui/widgets.popup_menu_over_video`: shown with `popup()`, never `exec()`.
Qt follows a right press with a context-menu event once the press is
handled, since 6.9 to the widget the pointer was over last; a menu run
inside the press let that event through only after a click on one of its
items - to the row under that item, whose own menu then opened (on
Wayland, where the compositor puts the pointer on the window under the
menu as it goes).

Switched off (Settings > Picture & sound, or `DOPEIPTV_VIDEO_THREAD=0`), or
when the own thread cannot start (no threaded OpenGL, no GL context or render
context, no frame shown), the old surface takes over: `_MpvGLWidget`
subclasses `QOpenGLWidget` and feeds frames through `MpvRenderContext` on
the window's thread. The reason is logged and shown in Nerd > Diagnostics
and the error report. `DOPEIPTV_GUI_RENDER=tuned` takes that surface's
waits off the window's thread.

mpv's render API calls (update, render, free) need the GL context current
on the calling thread. Never set `update_cb = None` on a live render
context: python-mpv drops the old callback before handing mpv the new one,
and mpv's thread may still be calling it.

macOS: Qt's own context cannot be used on the render thread there - its
`makeCurrent` puts the context on the window's view
(`-[NSOpenGLContext setView:]`), which AppKit allows on the main thread
only; on the render thread it stopped the process (SIGTRAP, tried on an M1
Pro with Qt 6.12). So the render thread draws with an `NSOpenGLContext` of
the app's own (`media/video_macos.py`), as Apple documents multithreaded
OpenGL and as SDL's context does: made on the main thread (OpenGL 4.1 core,
double-buffered, a hardware renderer first), put on the native window's
`NSView` there (`setView`, Retina's backing pixels asked for) and brought up
to date there (`update`) after every change of the view's size, place,
window, screen or scale; on the render thread only made current, drawn into
at the view's size in backing pixels and flushed. Both sides hold the
context's CGL lock; the window's thread only tries the context's gate and
makes a change a moment later when a frame is being drawn, so it never
waits for the picture. Every flush and update of every context is made one
at a time (NSOpenGLContext is not re-entrant even across contexts - Qt's
own does the same), so the flush does not wait for vsync (swap interval 0,
or one multiview cell would hold up the others) and mpv's render does not
wait for the frame's display time under the lock: the core gets mpv's
timing offset 0 instead (`build_core`'s *tuned*), so each frame is handed
over and flushed at its time. Anything that fails there - no PyObjC, no
pixel format, a view that would not take the context, an error while
drawing - is a reason, and the window's thread takes over.

On macOS the picture's native window is not a Qt child window but an
NSView of the app's own (`video_macos.PictureView`) over a plain
placeholder widget, kept in place by `video_thread._Placement`. Two things
a child QWindow did wrong there, both seen on a Mac: Qt's cocoa plugin
takes the child window under the pointer for the one a mouse move is for
(`QCocoaWindow::childWindowAt` looks at exposure, not at
`WindowTransparentForInput`) and drops the move, so nothing over the
picture saw hover - the controls only came up on a click; and a right-click
menu made native (`popup_menu_over_video`, against a QOpenGLWidget's bleed)
made its siblings native too - the main window's central widget got a
native view of its own, stacked over the picture's: black until the app
closed, while the render thread drew on underneath. The app's own view says
nil to `hitTest:` and has no tracking area (the mouse is Qt's alone), is
put into the NSView of its placeholder's nearest native ancestor at its
place there and on top of that view's subviews, moved with each ancestor's
move or resize, re-homed when an ancestor is made native, hidden with its
placeholder, taken out before the window's own view goes; and a menu over
the video goes native alone while the picture may have its own thread
(`video_thread.native_menu`, Qt's `AA_DontCreateNativeWidgetSiblings` around
it). On by default there only in a test build (a nightly, or run from a
checkout): a release on a Mac draws on the window's thread unless the
person turns the own thread on (`video_mode.default_on`); Diagnostics says
so in a neutral grey.

A window macOS reports occluded or minimized is not exposed and gets no
frames (mpv is told they were skipped), so a hidden window never swaps
into a surface vsync would hold; on macOS that is the top-level window's
exposure (Qt follows the NSWindow's occlusion there). Only on Wayland does
a covered window still count as seen (the compositor stops its frame
callbacks, as under the fullscreen window). The release build's mac smoke test runs
`--video-check`: a generated test picture drawn on the render thread
through that context (its renderer and the result in the run's
annotations), or skipped (exit 3) where the runner cannot try.

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
  `core/net_bridge.py`, a loopback proxy set as the player's `http-proxy`
  and a recorder's `http_proxy` (its environment, never its arguments). A
  recorder - and every other program the app starts on a stream: a cast's
  converter, server mode's HLS copy (`hand_over`) - always goes through it,
  for the guard on where each connection lands (`Policy`) and the alias it
  reads (`Bridge.alias`), route or none; the player on a route, and for
  every http(s) stream of a source on the internet with no route of its
  own (`player_proxy`). Putting the IP in the address with a Host header would work
  for plain http only: ffmpeg takes the TLS server name from the address, so
  an https stream would send no SNI and a CDN would refuse it, and a
  redirect or a playlist segment on another host would get the wrong Host.
  Through a proxy, ffmpeg asks `CONNECT host:port` for https and does TLS
  itself (name intact); plain http comes as an absolute address, sent on
  with `Connection: close` so every request is resolved for its own host.
  mpv does not check certificates (`tls-verify` is off by default), so for a
  pinned source the bridge makes a TLS connection of its own before it opens
  a tunnel and refuses it when the fingerprint differs (rechecked every
  ten minutes). A newer mpv or ffmpeg is told not to check that stream's
  chain itself (`checks_pin`), which would cover its redirects and HLS
  segments on other hosts too - so on a route with a pin the bridge checks
  every other https host against the system's authorities, for its name,
  before that tunnel opens, and refuses it when the check fails.
- **Not covered**: rtmp / rtsp / udp streams and multiview cells use the
  system's resolver; names only the home network knows are never sent to a
  public DNS; a custom DoH server's own name is resolved by the system (the
  presets are addressed by IP).

## Places

Since 1.6.2 the sidebar is the app's structure and there is no Home: Mine,
TV, Movies, Series, Guide, Search, then Local files, Radio, Recordings
(`core/places.py`, `ui/mw_places.py`). `_show_place(name)` is the one way to
a place; a place with a page of its own answers through its hook
(`_mitt_show`, `_place_tv_show`, `_place_films_show`, `_place_series_show`,
each True once shown), else the place shows its list under the shared
header (`ui/media_cards.PageHeader`). Every place uses the same painted
pieces (`ui/media_cards.py`): one view of uniform cells per list, pictures
through the loaders only for what is on screen. The app opens where the
person left off (`last_place`, each profile's own) or always on Mine
(`start_place`); Home's settings were carried over once
(`places.retire_home`). A film has no page of its own: in Movies a click
shows it in the detail column (its plot, cast and Continue with how far it
got) and a double click or Enter plays it (`ui/mw_place_films.py`,
`ui/mw_detail.py`); a series opens its page (`ui/mw_series_page.py`).

Popular right now (the top of Movies and of Series) comes from
`services/popular.py` through `ui/mw_popular.py`: TMDB's weekly chart
matched against the provider's catalogue on a worker. It needs a TMDB key:
release builds bundle one (`dopeiptv/_tmdb_key.py`, generated in CI and
git-ignored); a source checkout uses the user's own key from Settings →
Posters & info or `DOPEIPTV_TMDB_KEY`, and without any key the rows are
simply left out.

## EPG caching

`XmltvGuide` caches parsed XMLTV data to disk per playlist with a 6-hour
TTL.  On startup it loads stale cache first (instant UI), then refreshes
in the background.  Cache files live in the platform's cache directory.

## Theme system

`THEMES` in `ui/theme.py` (27 today, six of them light, every one for
everyone) and `ACCENTS`
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

The suite holds the app's performance to budgets too - the start's import
chain and time, the idle window's wakes, what is left after hundreds of
zaps, place switches and windows opened, a big guide and list
(`tests/test_perf_*.py`). [`PERFORMANCE.md`](PERFORMANCE.md) has the
numbers, how to run them longer (`DOPEIPTV_PERF_LONG=1`) and how to look
into one that fails.

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
would sit beside the cast card's, and its play plays on the Mac. The panel
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
the cast card (`ui/cast_card.py`) drives it like any cast. `NSBonjourServices` (`_airplay._tcp`,
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
is ready). The cast card has no volume for AirPlay - AVPlayer's has no
effect while the Apple TV plays the video, its remote sets it (the phone
remote hides its volume too: `vol_off`) - and its square AirPlay button
brings the AirPlay window back.

A live channel plays live on an Apple TV: going back in it - the rewind
buffer, the archive, the steps and the timeline, from the card, the phone
remote or the media keys - is not offered there (`_cast_can_go_back` in
`ui/mw_cast.py`, since 1.6.5), and a channel watched behind live is cast
live. Each step back was a new stream the Apple TV started over: slow, and
its sound out of step with the picture. Pausing, films and episodes work as
before, and a Chromecast still goes back.

Why not elsewhere: pyatv was tried on real hardware in July 2026 (an Apple
TV 4K pairs and connects, `play_url` fails with HTTP 500 on
`/playback-info` even for Apple's own demo stream; tvOS 18 broke the legacy
AirPlay video API for third-party senders, [pyatv
#2512](https://github.com/postlund/pyatv/issues/2512)). Only Apple's own
stack still sends video, so Linux and Windows have no AirPlay entry.

