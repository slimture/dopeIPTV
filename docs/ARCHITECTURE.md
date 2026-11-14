# Architecture

dopeIPTV is a desktop IPTV client built with PyQt6 and libmpv that
connects to Xtream Codes providers, M3U playlists and Stalker / Ministra
portals.  The codebase is grouped into layered subpackages inside the
`dopeiptv/` package - `providers/` (data sources), `core/` (storage,
background work, OS glue), `media/` (playback), `services/`
(window-agnostic application services) and `ui/` (everything the user
sees).

## Module map

```
dopeiptv.py            Thin launcher (calls dopeiptv.app.main)

dopeiptv/
  __init__.py          Package metadata (APP_NAME, ORG, VERSION)
  app.py               QApplication bootstrap, login flow
  i18n.py              English source strings + set_language / tr()
  locale/              One <code>.json per non-English language (26 files)

  providers/           Data sources (the network / provider edge)
    client.py          XtreamClient, OfflineClient/M3UClient/DemoClient
    stalker.py         StalkerClient - Stalker / Ministra MAC portals
    epg.py             XmltvGuide - XMLTV parser with disk cache + index
    epg_index.py       The guide parser as a process of its own (--epg-index)
    metadata.py        TMDB client, PosterResolver (cover art)
    trakt.py           Trakt.tv sync (watched, watchlist)
    oauth_loopback.py  Loopback HTTP server for the Trakt OAuth redirect
    diagnostics.py     Plain-language "why won't this stream play?" probe
    chromecast.py      ChromecastManager, CastDialog
    dlna.py            DLNA / UPnP TVs: SSDP discovery, SOAP control, file share
    casting.py         CastHub - Chromecast and DLNA behind one object
    cast_bridge.py     Local bridge that re-encodes the audio a Chromecast
                       refuses (E-AC-3) so the stream still casts
    opensubtitles.py   OpenSubtitles.com REST client (search, download, hash)

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
    series_rules.py    Series recordings: rules, airings found, the plan, padding
    updates.py         GitHub latest-release check (is_newer, fetch)
    selfupdate.py      AppImage in-place update: download, verify, test, swap
    wakelock.py        Screensaver/suspend inhibitor (DBus + caffeinate)
    gcguard.py         Cyclic garbage collection on the GUI thread only
    contest.py         ContestWatch: another device taking the account's connection
    health.py          Nerd's stream health per channel (it only watches)
    channel_prefs.py   Nerd: a channel's own audio/subtitle language,
                       deinterlace, aspect, audio delay (what each sets in mpv)
    backup.py          Full backup and restore (one JSON file; a restore over
                       everything is put in place as the app next starts)
    events.py          Nerd: events pushed out - webhooks and MQTT (queue +
                       a thread per receiver; it only watches)
    mqtt.py            A small MQTT 3.1.1 client (standard library only)
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
    notify.py          Desktop notifications (org.freedesktop.Notifications)
    popular.py         Popular right now: TMDB's chart of the week matched
                       against the provider's catalogue (Home shelves)

  ui/                  Everything the user sees
    __init__.py        Lazy MainWindow export (PEP 562)
    main_window.py     MainWindow composition root (QMainWindow + mixins)
    mw_settings.py     Settings dialog mixin (the pages and what they store)
    settings_ui.py     Settings building blocks: sidebar shell, pages,
                       cards, rows, switches, the playlist delegate
    qt_i18n.py         Qt's own strings (standard buttons, calendars) and
                       QLocale-formatted dates in the app's language
    mw_trakt.py        Trakt / watched / watchlist mixin
    mw_recording.py    Recording / timeshift mixin
    mw_portal_archive.py A Stalker portal's archive: by programme, its link
                       asked for off the window's thread
    mw_context.py      Right-click context-menu mixin
    mw_favorites.py    Favorites in bulk: multi-row actions, move, drag-and-drop
    mw_detail.py       EPG / detail-panel mixin
    mw_search.py       Sidebar category / item search mixin
    mw_sidebar.py      Sidebar chrome (collapse / icon rail / mid-compact)
    mw_nav.py          Nav chrome (focus mode, nav colours/icons, Library)
    mw_shortcuts.py    Keyboard shortcuts: dispatch, the keys in force, live rebind
    mw_nav.py          Nav chrome (focus mode, nav colours/icons, MINE,
                       History's Watched filter, the nav's status)
    mw_shortcuts.py    Keyboard shortcuts + registry
    mw_cast.py         Casting: the cast dialog, the strip, archive hand-over
    mw_popout.py       Detached-window ("pop out") player mixin
    mw_multiview.py    Multiview: 2/4/6/9 live streams (own mpv per cell)
    mw_busy.py         Loading indicator + spinner overlay
    mw_updates.py      Update-check badge/status wiring, "Update now"
    mw_reminders.py    Programme-reminder UI (set / list / fire)
    mw_series.py       Series recordings: "Record series…", the rules' check
    mw_mpris.py        Desktop media controls (MPRIS host) + media keys
    mw_follow.py       Follow a series: new-episode checks and news
    mw_subtitles.py    Subtitles from OpenSubtitles; remembered per title
    mw_home.py         The Home page (shelves: resume, favourites, new)
    home_widgets.py    Home's featured strip, shelves and cards
    mw_local.py        Local files: folders, library view, music queue
    mw_onboarding.py   First-run setup wizard
    mw_sort.py         List sort order
    widgets.py         Small standalone widgets
    nav_widgets.py     A nav entry (selected bar, status) and the rail's hairline
    category_flags.py  Bundled SVG flags for category names (flag-icons, MIT)
    player_resize.py   Height handle under the docked video
    welcome.py         First-run welcome overlay (connect / explore)
    channel_list.py    Virtualized list: model, view, painted delegate
    dialogs.py         Playlist editor, content manager
    epg_grid.py        EPG timeline grid view
    epg_search.py      Programme search across the whole guide
    reminders.py       Reminders manager dialog
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
   the download and LIVE never reconnects.  `media/dvr_player.py` is the
   player's side of it, `media/rewind_bar.py` the bar over the video.

6. **Recording** uses `RecordingManager`: ffmpeg stream-copy subprocess
   for standalone recordings, mpv's `--stream-record` for in-player
   capture, or `BufferRecorder` (`dvr_record.py`) reading from the app's
   own buffer when that is on - no extra connection, and whole while the
   player is paused.

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
- ffmpeg CLI (standalone recording; bundled in every packaged build)

Everything else (DLNA, MPRIS over D-Bus, OpenSubtitles, TMDB, Trakt, the
Stalker portal protocol, music tags) is written against the standard
library and `requests`.
