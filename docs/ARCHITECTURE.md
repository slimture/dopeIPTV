# Architecture

dopeIPTV is a desktop IPTV client built with PyQt6 that connects to
Xtream Codes providers.  The codebase is grouped into layered
subpackages inside the `dopeiptv/` package - `providers/` (data
sources), `core/` (storage, background work, OS glue), `media/`
(playback) and `ui/` (everything the user sees).

## Module map

```
dopeiptv.py            Thin launcher (calls dopeiptv.app.main)

dopeiptv/
  __init__.py          Package metadata (APP_NAME, ORG, VERSION)
  app.py               QApplication bootstrap, login flow, icon generation
  i18n.py              Translation tables + set_language / tr()

  providers/           Data sources (the network / provider edge)
    client.py          XtreamClient, OfflineClient/M3UClient/DemoClient
    epg.py             XmltvGuide - XMLTV parser with disk cache + index
    metadata.py        TMDB client, PosterResolver (cover art)
    trakt.py           Trakt.tv sync (watched, watchlist)
    oauth_loopback.py  Loopback HTTP server for the Trakt OAuth redirect
    chromecast.py      ChromecastManager, CastDialog

  core/                Storage, background work, OS integration
    stores.py          JSON data stores (playlists, favorites, history, ...)
    workers.py         QThreadPool helpers, LogoLoader, byte-bounded caches
    recording.py       RecordingManager - ffmpeg/mpv stream-copy, timers
    updates.py         GitHub latest-release check (is_newer, fetch)
    wakelock.py        Screensaver/suspend inhibitor (DBus + caffeinate)
    log.py             Central logging (configure_logging, DOPEIPTV_LOG)
    platform_macos.py  macOS-specific helpers (OpenGL, libmpv, paths)
    platform_windows.py Windows helpers (libmpv discovery, wakelock, shortcut)
    player_exec.py     Locate the external mpv / VLC binary on the host

  media/               Playback
    embedded.py        _MpvGLWidget(QOpenGLWidget), EmbeddedPlayer, seek bar
    players.py         External player launch (mpv IPC / VLC), libmpv detect

  services/            Window-agnostic application services
    coverart.py        CoverArtService - TMDB resolver lifecycle + the
                       ordered list/detail cover-candidate logic
    reminders.py       ReminderStore - programme reminders (persistence)
    resume.py          ResumeStore - per-playlist resume positions
                       (persistence + keep/drop rule)

  ui/                  Everything the user sees
    __init__.py        Lazy MainWindow export (PEP 562)
    main_window.py     MainWindow composition root (QMainWindow + mixins)
    mw_settings.py     Settings dialog mixin
    mw_trakt.py        Trakt / watched / watchlist mixin
    mw_recording.py    Recording / timeshift mixin
    mw_context.py      Right-click context-menu mixin
    mw_detail.py       EPG / detail-panel mixin
    mw_search.py       Sidebar category / item search mixin
    mw_sidebar.py      Sidebar chrome (collapse / icon rail / mid-compact)
    mw_nav.py          Nav chrome (focus mode, nav colours/icons, Library)
    mw_shortcuts.py    Keyboard shortcuts + registry
    mw_popout.py       Detached-window ("pop out") player mixin
    mw_busy.py         Loading indicator + spinner overlay
    mw_updates.py      Update-check badge/status wiring
    mw_reminders.py    Programme-reminder UI (set / list / fire)
    widgets.py         Small standalone widgets
    welcome.py         First-run welcome overlay (connect / explore)
    channel_list.py    Virtualized list: model, view, painted delegate
    dialogs.py         Login, playlist editor, content manager
    epg_grid.py        EPG timeline grid view
    epg_search.py      Programme search across the whole guide
    reminders.py       Reminders manager dialog
    shortcuts.py       Keyboard-shortcuts editor dialog
    tmdb_match.py      Manual TMDB match dialog
    theme.py           9 themes x 7 accents, palette dict P, QSS generator
```

Import direction is one-way: `ui` may use `services`, `media`, `core`
and `providers`; `services` builds on `providers`/`core` but never
imports `ui`; `media` and `providers` may use `core`; `core` depends on
nothing above it.  (The one cross-edge is `media.embedded` -> `ui.theme`
for player-overlay colours, which is why `ui/__init__` exposes
`MainWindow` lazily so touching `ui.theme` never drags in the window.)

## Data flow

1. **Startup** (`app.py`): creates QApplication, applies theme, runs the
   login loop (PlaylistStore -> XtreamClient.authenticate), then builds
   MainWindow.

2. **MainWindow** loads categories and channels from the Xtream API via
   `run_async()` workers.  Results populate `ChannelListModel`; the
   `ChannelDelegate` custom-paints each row (list mode) or cell (grid).

3. **EPG** is fetched in a background worker into `XmltvGuide`, which
   caches parsed XML to disk (per-playlist, 6h TTL, stale-first).

4. **Playback** goes through either `EmbeddedPlayer` (libmpv OpenGL
   render API inside a QOpenGLWidget) or an external player launched by
   `players.py`.

5. **Recording** uses `RecordingManager`: ffmpeg stream-copy subprocess
   for standalone recordings, or mpv's `--stream-record` for in-player
   capture.

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
- python-mpv (optional, for embedded playback)
- pychromecast (optional, for Chromecast)
- ffmpeg CLI (optional, for standalone recording)
