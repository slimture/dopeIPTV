# dopeIPTV

An elegant IPTV client for Linux and macOS with a macOS-inspired dark interface, supporting the **Xtream Codes API**, **EPG**, and playback via **mpv** or **VLC**.

## Features

- Log in with Xtream Codes credentials (server, username, password) — settings are saved
- TV, Movies, and Series with seasons and episodes, plus Favorites and History
- Categories in the sidebar and fast, debounced search — no cap on the number of channels, and no freeze even with thousands of them (the channel list is virtualized, so only visible rows are ever rendered)
- EPG shown immediately for every visible live channel (not just the one you click), using a "now playing" line and progress bar
- EPG Guide dialog: browse now/next info for every channel in one searchable list
- EPG fallback chain: short EPG → full EPG table (`get_simple_data_table`) → the provider's XMLTV guide (`xmltv.php`), matched by EPG channel id or channel name — listings are still shown when the server's timestamps look skewed, and duplicate entries from buggy providers are filtered out
- Refresh the EPG with one click — it also refreshes automatically when the current program ends
- Movie and series details in the detail panel: plot, genre, cast, director, release date, and rating
- Favorites: save channels into your own groups, browse them under Favorites, remove channels or whole groups via right-click
- History: every played channel/movie/episode is recorded with a resolved playback URL; browse, replay, remove entries (with full **multi-select**: Ctrl/Shift-click, Ctrl+A, rubber-band drag, then right-click → remove or press Delete), or clear everything
- A visible loading bar while categories/content are being fetched
- **Embedded in-app video** (Linux and macOS): with `python-mpv` + libmpv installed, channels play directly inside the app's detail panel, rendered via libmpv's OpenGL render API — no window embedding involved, so it works the same regardless of desktop/compositor (GNOME, KDE, Hyprland, ...) and is the default whenever available. Double-click the video or press `F` for fullscreen, `Esc` to leave it. Selecting a new channel switches the stream in place — perfect for zapping through favorites.
- **Player controls**: pause/resume plus −10s / +30s skip buttons for seekable content (movies, series, catch-up), in both the mini player bar and the fullscreen floating controls
- **In-player options menu** (the ⚙ button): pick the audio track (language) and subtitle track, adjust audio delay, force an aspect ratio (16:9, 4:3, stretch, ...), and set the network buffer/cache size — applied live and remembered.
- **Permanent playback defaults** under Settings → Playback: preferred audio language, subtitles off/on (+ preferred subtitle language), default aspect ratio, and network buffer — applied to every stream automatically; the ⚙ menu still overrides per session.
- **Stays awake**: while video plays (mini player or fullscreen) the app inhibits the screensaver/suspend via DBus (GNOME/KDE) or `caffeinate` on macOS, and releases it on stop/error/exit
- Changing the aspect ratio letterboxes **inside** the player: the mini player's video is pinned to a **constant height** — dragging the splitter, resizing the window, long titles or EPG cards never change its size
- **Themes**: five color themes (Graphite, Midnight blue, OLED pure black, Nord, Light) and seven accent colors (blue/purple/teal/green/orange/pink/red) under Settings → Interface — applied live, no restart
- Fullscreen niceties: an **"Exit fullscreen" button** right in the floating controls (no need to know about `Esc`), the mouse cursor auto-hides with the controls, and leaving fullscreen returns the channel list to the playing channel instead of jumping to the top
- The window title shows what's currently playing, and the **playing item is highlighted** in every list — TV, Movies, series episodes, Favorites, Recordings and History, in both list and grid view (accent bar/border + colored name)
- **Back / Next** buttons in the player bar zap not only through TV channels but also through Movies, a series' episodes, Recordings and History entries
- **Recording**: right-click a TV channel → Record (or use the **REC button right in the player**) to record the stream locally — **until you stop it**, for a fixed length (30 min / 1 h / 2 h / 4 h), or **scheduled with start and stop timers**. Saved where you choose (Settings → Recording); scheduled jobs survive an app restart (the app must be running when they fire)
- **Recording the channel you're watching uses the same stream** — it taps the embedded player's own connection (mpv stream-record) instead of opening a second one, so it works on the common one-stream-at-a-time IPTV accounts. Recording a *different* channel, or a scheduled job firing later, records over its own connection (ffmpeg, mpv fallback). Note: the file of an in-player recording is finalized when the recording stops — stopping playback, zapping away, or a stream error ends it
- A persistent red **● REC indicator** under the channel list shows whenever something is recording — click it to stop a recording or jump to the Recordings section. The player's REC button also turns into ● REC and its menu has **stop actions** for every active recording
- **Stream-switch protection**: starting another stream while something records pops a clear choice — **watch the recorded channel** (plays the recording file, no new connection), **stop the recording and switch**, or **play anyway** if your account allows several streams (remembered for the session). Whenever a recording ends — planned or not — a clear notice appears in the status bar (and on the video in fullscreen)
- **Recording size cap**: optionally stop any recording once its file reaches a limit you set (number + MB/GB/TB) under Settings → Recording
- **Recordings section** in the sidebar: browse recordings with subfolders as categories, create folders, rename, move between folders, multi-select delete (right-click or the Delete key), sort with the usual Sort control — and watch a recording **while it's still being recorded**. "Active & scheduled" shows running/pending jobs with stop/cancel
- **Timeshift / catch-up detection**: channels whose provider keeps an archive (`tv_archive`) get a ⏪ marker in the list and a "Catch-up: N days" note in the detail panel; right-click → Timeshift — or the **⏪ button right in the player** (windowed and fullscreen) — to **watch the current programme from the start**, **browse past programmes from the EPG** (grouped by day, double-click to watch), or jump back in steps that **scale dynamically with the provider's actual archive depth** (30 min up to 7 days). The archive chunk is seekable like a movie
- **"Play in VLC" is always a one-off external launch** — it never changes the default player, so the embedded mini player keeps working right after
- **Edit your channel list**: right-click any channel/movie/series to rename or hide it (persisted per playlist — effectively your own edited playlist), and "Restore default channels..." undoes it all back to exactly what the provider sends. Right-clicking never switches the playing channel — only left-click selects
- Stream errors are shown in red in the status bar and cleared automatically as soon as something else plays
- mpv playback can also reuse a single external window instead: selecting a new channel loads it into the same mpv instance rather than opening a new one. With `python-mpv` installed, Ctrl+Right / Ctrl+Left zap to the next/previous channel **even while that mpv window has keyboard focus** — mpv itself intercepts the keys and calls back into the app, since Qt shortcuts alone only fire while the dopeIPTV window is focused. That window's own fullscreen (`f`) and quit (`q`) keys work normally, and the app's `F` shortcut also toggles its fullscreen. VLC and "open externally" still spawn a normal one-off process/window. Pick the mode under Settings → "Playback (mpv)".
- Channel logos loaded asynchronously and cached
- Proper application name and icon in the taskbar (instead of "python3"), an app menu with **About** in GNOME's top bar, and a version number
- Copy stream URL via right-click; "Open externally" in mpv, VLC, or **both at once** from the same menu
- Sort any list by provider order, name A→Z / Z→A, or recently added
- Three list sizes (compact / medium / large icons)
- Settings organized into tabs (Playback / Interface / Playlists)
- **Multiple playlists**: save several Xtream providers/accounts, switch between them at runtime (favorites and history are kept per playlist), edit them, give each an optional **custom TV guide (XMLTV) URL** that overrides the provider's own, and pick an **auto-refresh** cadence per playlist (never / at startup / every 2, 6, 12 hours / daily / weekly)
- Inline view controls right under the channel list: list size, sort order, and a grid toggle that lays large icons out horizontally — with smooth, fast per-pixel wheel scrolling in grid mode
- **Content manager**: right-click any category (or "Manage categories...") to hide it, rename it, or lock it — hidden/locked categories' channels are also left out of "All"
- **Parental control**: a salted+hashed PIN protects locked categories and locked favorite groups; locked favorite groups don't reveal which channels they contain until the PIN is entered. Locking something locks it immediately; unlocks last for the session, with a "Lock now" button in Settings → Parental
- **Chromecast**: right-click any channel/movie/episode → "Cast to Chromecast..." to scan the network and cast to a device (live streams are cast as HLS). Needs `pip install pychromecast`
- **Offline start**: if the active playlist's server can't be reached at launch you get a warning with "Start anyway" — the app opens and content loads once the server is reachable again
- Zap arrows (◀ ▶) in the embedded player's control bar; in fullscreen, floating zap buttons appear on mouse movement (bottom-right, auto-hiding with the info overlay) and the plain Left/Right arrow keys zap channels
- **Seek bar** in the embedded player for movies, series and catch-up content: click or drag the timeline to jump, with elapsed/total time shown — in both the windowed pane and the fullscreen floating controls. Live streams (which aren't seekable) keep the bar hidden
- **Persistent EPG cache**: the XMLTV guide is saved to disk per playlist, so after a restart the EPG shows immediately from the previous session (a cache younger than 6 hours skips the download entirely). When the cache is older, **last session's guide is shown instantly** while the fresh download replaces it in the background — and if the download fails, the stale data stays. The terminal log says which source was used
- **EPG refresh with progress**: "Refresh EPG" force re-downloads the guide with a real progress bar (percent when the server reports a size, animated otherwise) and then updates both the panel and the now-playing lines in the channel list
- Finds VLC on macOS even when it's only installed as an app bundle (not on `PATH`)

## Installation

```bash
# Dependencies (ffmpeg is optional - it's the preferred recorder for the
# Record feature; without it, mpv records instead)
sudo apt install python3 python3-pip mpv vlc ffmpeg   # Debian/Ubuntu
# or: sudo dnf install python3 mpv vlc ffmpeg          # Fedora
# or: sudo pacman -S python mpv vlc ffmpeg             # Arch
# or on macOS: brew install mpv ffmpeg && brew install --cask vlc

pip install PyQt6 requests

# Optional but recommended - enables embedded in-app video (Linux and macOS):
pip install python-mpv

# Optional - enables Chromecast casting:
pip install pychromecast
```

> **Embedded playback platform notes:** in-app video is rendered via
> libmpv's OpenGL render API directly into a Qt OpenGL widget, not via
> window embedding — so it doesn't depend on X11 vs. Wayland or on any
> particular compositor/window manager, and it's the same technique usable
> on macOS. It just needs `python-mpv` (which needs libmpv itself, e.g. via
> `brew install mpv` on macOS) and a working OpenGL context, which Qt
> provides everywhere. If it's unavailable for any reason, playback falls
> back to the reused external mpv window automatically.

## Running

```bash
python3 dopeiptv.py
```

The first time, enter your Xtream server (e.g. `http://server:8080`), username, and password.

## Add to the application menu (optional, Linux)

```bash
mkdir -p ~/.local/share/applications ~/.local/bin
cp dopeiptv.py ~/.local/bin/dopeiptv.py
cp dopeiptv.desktop ~/.local/share/applications/
```

The application icon is installed automatically to `~/.local/share/icons` the first time the app starts.

## Usage

| Action | How |
|---|---|
| Play | Double-click, or the "Play in mpv / VLC" buttons |
| Pause / skip | ⏸ and −10s / +30s in the player bar (skips appear for seekable content) |
| Audio track / subtitles / aspect / buffer / audio delay | The ⚙ button in the player bar (also in fullscreen) |
| Fullscreen | Double-click the embedded video, or press `F` — `Esc` or the "Exit fullscreen" button to leave (also toggles fullscreen on the reused mpv window) |
| Zap to next/previous channel | Ctrl+Right / Ctrl+Left |
| Rename / hide a channel | Right-click it (restore with "Restore default channels...") |
| Record a channel | Right-click it → Record (or the REC button in the player) → until stopped, 30 min–4 h, or "Schedule recording..." |
| Stop a recording | Click the red ● REC indicator under the list (or Recordings → Active & scheduled) |
| Change theme / accent color | Settings → Interface |
| Manage recordings | Recordings in the sidebar: folders as categories, right-click to rename/move/delete, Delete key works |
| Watch a programme from the start / an old programme | Right-click a ⏪ channel (or the ⏪ button in the player) → Timeshift / catch-up |
| Change list size / sort / grid | The Size, Sort and Grid controls above the list — no need to open Settings |
| Switch player temporarily | Right-click a row |
| Open a series | Double-click the series → episode list appears |
| Search | Type in the search field at the top |
| Refresh EPG | The "Refresh EPG" button in the detail panel |
| Browse the full TV guide | "EPG Guide" button in the sidebar |
| Add a favorite | Right-click a channel → "Add to favorites group" |
| Remove a favorite / group | In Favorites: right-click the channel or the group |
| Remove / clear history | In History: select one or many (Ctrl/Shift-click, Ctrl+A, drag), then right-click or press Delete — or "Clear history" |
| Switch account | Settings → "Switch account / server" |

## Troubleshooting

- **"Player not found"** — install mpv or VLC (see above). On macOS, VLC is detected even if it's only in `/Applications` and not on `PATH`.
- **Live stream won't start** — try switching the live format to `m3u8` in Settings.
- **No categories** — check the server URL (include the port, e.g. `:8080`).
- **No EPG for a channel** — the provider may not have a programme guide mapped to it at all; dopeIPTV already tries three different sources (short EPG, full EPG table, XMLTV) before giving up.
