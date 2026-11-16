# Architecture

dopeIPTV is a desktop IPTV client built with PyQt6 and libmpv that
connects to Xtream Codes providers, M3U playlists, Stalker / Ministra
portals, boxes on the home network and Jellyfin, Emby and Plex servers.
The codebase is grouped into layered subpackages inside the
`dopeiptv/` package - `providers/` (data sources), `core/` (storage,
background work, OS glue), `media/` (playback), `services/`
(window-agnostic application services) and `ui/` (everything the user
sees).

## Module map

```
dopeiptv.py            Thin launcher (calls dopeiptv.app.main; --server: headless)

dopeiptv/
  __init__.py          Package metadata (APP_NAME, ORG, VERSION)
  app.py               QApplication bootstrap, login flow
  launcher.py          The installed command: app.main, or headless with --server
  headless.py          Server mode without a window (services/home_server.py):
                       QtCore for the settings, no widget, no mpv
  i18n.py              English source strings + set_language / tr()
  locale/              One <code>.json per non-English language (26 files)

  providers/           Data sources (the network / provider edge)
    client.py          XtreamClient, OfflineClient/M3UClient/DemoClient
    stalker.py         StalkerClient - Stalker / Ministra MAC portals
    mediaserver.py     MediaServerClient - what Jellyfin, Emby and Plex share:
                       a token, one device id per install, films / series /
                       channels as the app's rows, reports back on a thread
    jellyfin.py        JellyfinClient / EmbyClient - sign-in, Quick Connect,
                       libraries, Live TV, UDP 7359 discovery
    plex.py            PlexClient - PIN sign-in, servers, libraries, DVR, GDM
    epg.py             XmltvGuide - XMLTV parser with disk cache + index
    epg_index.py       The guide parser as a process of its own (--epg-index)
    metadata.py        TMDB client, PosterResolver (cover art)
    trakt.py           Trakt.tv sync (watched, watchlist)
    oauth_loopback.py  Loopback HTTP server for the Trakt OAuth redirect
    diagnostics.py     Plain-language "why won't this stream play?" probe
    chromecast.py      ChromecastManager, CastDialog
    dlna.py            DLNA / UPnP TVs: SSDP discovery, SOAP control, file share
    casting.py         CastHub - Chromecast, DLNA and Kodi behind one object
    kodi.py            Kodi as a cast target over JSON-RPC (HTTP)
    mdns.py            mDNS discovery in plain Python (finds Kodi)
    simkl.py           Simkl sync (scrobbles, watched, Plan to watch)
    cast_bridge.py     Local bridge that re-encodes the audio a Chromecast
                       refuses (E-AC-3) so the stream still casts
    airplay_bridge.py  AirPlay: what goes to an Apple TV as it is, the rest
                       as HLS by ffmpeg on the LAN (token, cleanup)
    opensubtitles.py   OpenSubtitles.com REST client (search, download, hash)
    radio_browser.py   Internet radio from radio-browser.info: servers from
                       its DNS round robin, falling back between them

  core/                Storage, background work, OS integration
    stores.py          JSON data stores (playlists, favorites, history, ...)
    workers.py         QThreadPool helpers, LogoLoader, byte-bounded caches
    local_buffer.py    Seekable window of a live channel's rewind buffer (DVR)
    dvr_store.py       App-kept rewind buffer: rolling TS segment store
    dvr_ingest.py      Its one provider connection
    dvr_server.py      Its loopback server for mpv (token, live TS)
    dvr_session.py     Ingest + store + server for one live channel
    dvr_pool.py        Every channel's buffer this session, one budget
    dvr_record.py      A recording read from a buffer
    recording.py       RecordingManager - ffmpeg/mpv stream-copy, timers
    rec_mp4.py         Recordings as MP4: the remux (stream copy, faststart),
                       the plausibility check, the .ts kept unless it passed
    series_rules.py    Series recordings: rules, airings found, the plan, padding;
                       a rule that reminds instead, its reminders' plan
    series_view.py     A series as its page shows it: seasons (Specials last),
                       the next episode, the season it opens on and the one kept
    night_mode.py      Night mode for the sound: one labelled audio filter
    viewing_stats.py   Your statistics: seconds watched per title and hour, the
                       sums (this computer only, never synced)
    updates.py         GitHub latest-release check (is_newer, fetch)
    selfupdate.py      AppImage in-place update: download, verify, test, swap
    wakelock.py        Screensaver/suspend inhibitor (DBus + caffeinate)
    gcguard.py         Cyclic garbage collection on the GUI thread only
    contest.py         ContestWatch: another device taking the account's connection
    connections.py     Connections to the account: the app's own counted, the
                       limit, a warning before one more would go over it
    mirrors.py         Nerd: a source's backup addresses - the next when the
                       one in use is down (never on a refused login)
    conn_test.py       Nerd: test the connection to a source step by step
                       (DNS, TCP, TLS, login, first byte, speed)
    health.py          Nerd's stream health per channel (it only watches)
    channel_prefs.py   Nerd: a channel's own audio/subtitle language,
                       deinterlace, aspect, audio delay (what each sets in mpv)
    backup.py          Full backup and restore (one JSON file; a restore over
                       everything is put in place as the app next starts)
    events.py          Nerd: events pushed out - webhooks and MQTT (queue +
                       a thread per receiver; it only watches)
    mqtt.py            A small MQTT 3.1.1 client (standard library only)
    mqtt_control.py    Nerd: Home Assistant's commands over MQTT (topics,
                       parsing, the controls' discovery, TV mode's switch)
    phone_remote.py    The phone remote's server (home network, token in the
                       address, rate limit and lock-out, nothing else)
    phone_page.py      The phone remote's page (HTML/CSS/JS, nothing fetched;
                       a remote with a touchpad while TV mode is on)
    qr.py              QR code encoder (byte mode, level M, versions 1-40)
    ical.py            Reminders and recordings as iCalendar (RFC 5545)
    search_all.py      One search across every source: rows as the lists show
                       them, a guide's week compact, four groups ranked
    shortcut_keys.py   Keyboard shortcuts: the actions, the app's keys, the
                       user's over them, never two on one key
    log.py             Central logging (configure_logging, DOPEIPTV_LOG)
    platform_macos.py  macOS-specific helpers (OpenGL, libmpv, paths)
    platform_windows.py Windows helpers (libmpv discovery, wakelock, shortcut)
    player_exec.py     Locate the external mpv / VLC binary on the host
    desktop_entry.py   The user's .desktop entry + icons (Wayland reads the
                       window icon from there, not from the app)
    xdg.py             Launch system programs from a frozen bundle without
                       our bundled libraries leaking into them
    tv_input.py        TV mode's input as actions: LIRC (lircd's socket), a
                       game controller (SDL2 if pysdl2 is there, else evdev)
    tv_nav.py          TV mode's focus moves (nearest element, grid step)
    ytdlp.py           yt-dlp for YouTube, Twitch and the like: the system's
                       or the app's own copy (release binary, SHA2-256SUMS
                       checked, updated at most once a day while used)
    audiotags.py       Artist/album/title and cover art from music files
                       (header-only, no dependency)
    _alloc.py          Optional allocator preload for long sessions
    _lazy_requests.py  Lazy stand-in for `requests` (faster cold start)

  media/               Playback
    embedded.py        EmbeddedPlayer: the in-app player
    seek_slider.py     The seek bar slider: click, drag, hover time
    video_surface.py   _MpvGLWidget(QOpenGLWidget), the surface mpv draws
                       into; the Linux pop-out's raster mirror
    rewind_bar.py      DVR row + timeshift timeline over the video
    dvr_player.py      The player's side of the app-kept buffer
    players.py         External player launch (mpv IPC / VLC), libmpv detect
    subtitle_text.py   Stray right-to-left marks out of subtitle files

  services/            Window-agnostic application services
    coverart.py        CoverArtService - TMDB resolver lifecycle + the
                       ordered list/detail cover-candidate logic
    reminders.py       ReminderStore - programme reminders (persistence)
    resume.py          ResumeStore - per-playlist resume positions
                       (persistence + keep/drop rule)
    mpris.py           MPRIS media session on the D-Bus session bus (Linux)
    media_session.py   The system's media controls: one host, the session
                       for this system (MPRIS / Now Playing / SMTC), the
                       poll, commands queued onto the GUI thread
    media_macos.py     macOS Now Playing (PyObjC's MediaPlayer)
    media_windows.py   Windows' System Media Transport Controls (PyWinRT)
    airplay_macos.py   AirPlay on macOS: AVPlayer + AVKit's route picker
                       in an NSPanel of its own, a CastHub receiver
                       (PyObjC's AVFoundation, AVKit, AppKit)
    notify.py          Desktop notifications (org.freedesktop.Notifications)
    search_sources.py  The search across sources: each source's client, lists
                       (a portal's only as loaded) and guide from disk, once
    popular.py         Popular right now: TMDB's chart of the week matched
                       against the provider's catalogue (Home shelves)

  ui/                  Everything the user sees
    __init__.py        Lazy MainWindow export (PEP 562)
    main_window.py     MainWindow composition root (QMainWindow + mixins)
    mw_settings.py     Settings dialog mixin (the pages and what they store;
                       which row is where: docs/SETTINGS_MAP.md)
    settings_ui.py     Settings building blocks: sidebar shell (groups, a
                       group's pages shown only while it is on), pages,
                       cards, rows, link rows, switches, the playlist delegate
    qt_i18n.py         Qt's own strings (standard buttons, calendars) and
                       QLocale-formatted dates in the app's language
    mw_trakt.py        Trakt / watched / watchlist mixin (the sync reads
                       every account signed in: Trakt, Simkl or both)
    mw_simkl.py        Simkl: scrobbles, its Settings card, PIN sign-in
    mw_discord.py      Discord status ("Watching ...") from the player
    mw_recording.py    Recording / timeshift mixin
    mw_portal_archive.py A Stalker portal's archive: by programme, its link
                       asked for off the window's thread
    mw_media.py        Media servers: what plays reported, their resume
                       points and watched marks (media_signin.py: signing in)
    mw_context.py      Right-click context-menu mixin
    mw_favorites.py    Favorites in bulk: multi-row actions, move, drag-and-drop
    mw_detail.py       EPG / detail-panel mixin
    mw_search.py       Sidebar category / item search mixin
    mw_sidebar.py      Sidebar chrome (collapse / icon rail / mid-compact)
    mw_nav.py          Nav chrome (focus mode, nav colours/icons, Library)
    mw_shortcuts.py    Keyboard shortcuts: dispatch, the keys in force, live rebind
    mw_nav.py          Nav chrome (focus mode, nav colours/icons, MINE,
                       History's Watched filter, the nav's status)
    mw_source_label.py The active source in plain sight: over the
                       categories, in the status line, TV mode's header
    mw_shortcuts.py    Keyboard shortcuts + registry
    mw_cast.py         Casting: the cast dialog, the strip, archive hand-over
    mw_airplay.py      AirPlay (macOS): the receiver added, its own endings
    mw_popout.py       Detached-window ("pop out") player mixin
    mw_multiview.py    Multiview: 2/4/6/9 live streams (own mpv per cell)
    mw_connections.py  Connections to the account: counted, warned about
    mw_mirrors.py      Nerd: a stream whose server is down, on the next address
    conn_test_dialog.py Nerd: Test connection's window
    mw_busy.py         Loading indicator + spinner overlay
    mw_updates.py      Update-check badge/status wiring, "Update now"
    mw_reminders.py    Programme-reminder UI (set / list / fire)
    mw_calendar.py     Reminders and recordings as a calendar (file, served)
    mw_phone.py        The phone remote: on/off, its requests on the window's thread
                       (TV mode's through mw_tv.tv_command, as the remote API's
                       and Home Assistant's)
    mw_series.py       Series recordings: "Record series…", the rules' check
    mw_mpris.py        The system's media controls (media_session host) +
                       media keys
    mw_stats.py        Your statistics: playback time counted, its window
    stats_window.py    The statistics window: tiles, two column charts, top lists
    mw_mpris.py        Desktop media controls (MPRIS host) + media keys
    mw_follow.py       Follow a series: new-episode checks and news
    mw_subtitles.py    Subtitles from OpenSubtitles; remembered per title
    mw_home.py         The Home page (shelves: resume, favourites, new)
    mw_guide.py        The Guide page in the middle pane, and its pop-out
    mw_series_page.py  A series' page in the middle pane: opened from anywhere,
                       played through the window's path, Back to the list as it was
    mw_search_all.py   The Search page in the middle pane: one search across
                       every source, a hit played or opened through the
                       window's path (another source's channel without a switch)
    search_page.py     The Search page itself: field, chips, grouped results
    series_page.py     The series page itself: header, season chips, the list
    series_cards.py    Its episode cards: model, painted card, keys, lazy pictures
    home_widgets.py    Home's featured strip, shelves and cards
    mw_local.py        Local files: folders, library view, music queue
    mw_radio.py        Radio: radio-browser.info's stations, played as music
    mw_webstreams.py   Open address, YouTube/Twitch through yt-dlp, saved
                       addresses
    mw_tv.py           TV mode: on/off, its keys and inputs, the picture with
                       the info bar, zapping (plays through the window's path)
    tv_mode.py         TV mode's page and screens (Home, Channels, Options)
    tv_screens.py      TV mode's guide, Movies/Series and Search screens
    tv_widgets.py      TV mode's tiles, lists, keyboard and info bar
    mw_onboarding.py   First-run setup wizard
    mw_sort.py         List sort order
    widgets.py         Small standalone widgets
    nav_widgets.py     A nav entry (selected bar, status), the rail's hairline,
                       the source's avatar at the foot, its name over the
                       categories
    category_flags.py  Bundled SVG flags for category names (flag-icons, MIT)
    player_resize.py   Height handle under the docked video
    welcome.py         First-run welcome overlay (connect / explore)
    channel_list.py    Virtualized list: model, view, painted delegate
    dialogs.py         Playlist editor, content manager
    epg_grid.py        The guide (EpgGrid): timeline grid, filter, bar -
                       on the Guide page or in its window (EpgGridDialog)
    reminders.py       Reminders manager dialog
    calendar_export.py Export to calendar… (an .ics, the subscription addresses)
    phone_remote.py    Phone remote… dialog: the code (QrView), the address
    shortcuts.py       Keyboard-shortcuts card (Settings > Advanced)
    tmdb_match.py      Manual TMDB match dialog
    subtitle_search.py OpenSubtitles search dialog
    theme.py           THEMES (27, two of them a supporter's) x ACCENTS
                       (10 + custom), palette dict P, QSS generator
    app_icon.py        The app's icon, plain or the supporter's: drawn,
                       set for every window, written to the icon theme
```

Import direction is one-way: `ui` may use `services`, `media`, `core`
and `providers`; `services` builds on `providers`/`core` but never
imports `ui`; `media` and `providers` may use `core`; `core` depends on
nothing above it.  (The one cross-edge is `media.embedded` -> `ui.theme`
for player-overlay colours, which is why `ui/__init__` exposes
`MainWindow` lazily so touching `ui.theme` never drags in the window.)

## Data flow

1. **Startup** (`app.py`): creates QApplication, applies theme, runs the
   login loop (PlaylistStore -> the playlist's client: `XtreamClient`,
   `M3UClient`, `StalkerClient` or `DemoClient`, then `authenticate`),
   then builds MainWindow.  A first run goes through the setup wizard
   (`mw_onboarding.py`) instead.

2. **MainWindow** loads categories and channels from the provider via
   `run_async()` workers (every client offers the same calls, so the
   window does not care which kind it has).  Results populate `ChannelListModel`; the
   `ChannelDelegate` custom-paints each row (list mode) or cell (grid).

3. **EPG** is fetched in a background worker into `XmltvGuide`, which
   caches parsed XML to disk (per-playlist, 6h TTL, stale-first).

4. **Playback** goes through either `EmbeddedPlayer` (libmpv OpenGL
   render API inside a QOpenGLWidget) or an external player launched by
   `players.py`.

5. **Rewind / DVR**: by default mpv's own demuxer cache is the rewind
   buffer (in memory, or on disk with `local_buffer.py`).  With
   "dopeIPTV's own buffer" on, `DvrPool` runs a `DvrSession` per live
   channel: `dvr_ingest` holds the one provider connection, `dvr_store`
   keeps rolling TS segments on disk within the space budget, and
   `dvr_server` serves them to mpv over loopback - so pausing never stalls
   the download and LIVE never reconnects.  Only a stream is kept: a
   connection that brings a few kilobytes and ends is a failed try, and an
   answer that is plainly no stream (a web page, JSON, a playlist) is given
   up at once - the pool then plays that channel without the buffer for a
   while.  An address opened by hand (Open address…) never gets the
   buffer: mpv opens it.  `media/dvr_player.py` is the player's side of
   it, `media/rewind_bar.py` the bar over the video.

6. **Recording** uses `RecordingManager`: ffmpeg stream-copy subprocess
   for standalone recordings, mpv's `--stream-record` for in-player
   capture, or `BufferRecorder` (`dvr_record.py`) reading from the app's
   own buffer when that is on - no extra connection, and whole while the
   player is paused. With "Save recordings as MP4" on, a finished .ts is
   remuxed into an MP4 off the window's thread (`rec_mp4.py`) and the .ts
   deleted only once the MP4 is plausibly whole.

## Key patterns

- **QSettings** stores all persistent state (playlists, favorites,
  history, category overrides, parental PIN, theme, recording prefs).
  The JSON stores in `stores.py` wrap QSettings keys.

- **Virtualized list** via QAbstractListModel + QStyledItemDelegate
  handles 10k+ channel lists without lag.

- **Logo loading** is lazy and async: `LogoLoader` fetches images in a
  thread pool and updates the viewport on completion.

- **Theme** is a palette dict `P` plus a `build_style()` function that
  generates the full QSS at startup or on theme change.

## External dependencies

- PyQt6 (widgets, OpenGL, core)
- requests (HTTP)
- python-mpv + libmpv (the embedded player - required in practice; the
  packaged builds bundle libmpv)
- pychromecast (Chromecast; bundled in every packaged build)
- PyObjC's MediaPlayer wrapper on macOS and PyWinRT's Windows.Media packages
  on Windows (the system's media controls; installed and bundled only on
  their own system - the app runs without them); PyObjC's AVFoundation and
  AVKit on macOS for AirPlay, the same way
- ffmpeg CLI (standalone recording, the cast and AirPlay bridges; bundled in
  every packaged build)

Everything else (DLNA, Kodi and mDNS, MPRIS over D-Bus, Discord's IPC,
OpenSubtitles, TMDB, Trakt, Simkl, the Stalker portal protocol, music tags) is written against the standard
library and `requests`.
