# Architecture

dopeIPTV is a desktop IPTV client built with PyQt6 that connects to
Xtream Codes providers.  The codebase is split into focused modules
inside the `dopeiptv/` package, each under ~500 lines (except the
main window which is inherently larger as a single QMainWindow class).

## Module map

```
dopeiptv.py            Thin launcher (calls dopeiptv.app.main)

dopeiptv/
  __init__.py          Package metadata (APP_NAME, ORG, VERSION)
  app.py               QApplication bootstrap, login flow, icon generation
  main_window.py       MainWindow(QMainWindow) - all UI and business logic
  channel_list.py      Virtualized list: model, view, custom-painted delegate
  dialogs.py           Login, playlist editor, EPG guide, content manager
  embedded.py          _MpvGLWidget(QOpenGLWidget), EmbeddedPlayer, seek bar
  players.py           External player launch (mpv IPC / VLC), libmpv detection
  recording.py         RecordingManager - ffmpeg/mpv stream-copy, timers
  client.py            XtreamClient - Xtream Codes HTTP API wrapper
  epg.py               XmltvGuide - XMLTV parser with disk cache
  stores.py            JSON data stores (playlists, favorites, history, etc.)
  workers.py           QThreadPool helpers, LogoLoader
  chromecast.py        ChromecastManager, CastDialog
  theme.py             5 themes x 7 accents, palette dict P, QSS generator
  wakelock.py          Screensaver/suspend inhibitor (DBus + macOS caffeinate)
  platform_macos.py    macOS-specific helpers (OpenGL, libmpv, wakelock, paths)
```

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
