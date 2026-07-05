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
- History: every played channel/movie/episode is recorded with a resolved playback URL; browse, replay, remove individual entries, or clear everything
- A visible loading bar while categories/content are being fetched
- **Embedded in-app video** (Linux, X11 desktops): with `python-mpv` + libmpv installed, channels play directly inside the app's detail panel. Double-click the video or press `F` for fullscreen, `Esc` to leave it. Selecting a new channel switches the stream in place — perfect for zapping through favorites. Not reliable on GNOME/Mutter (see the platform note below), where the reused-window mode is used instead by default.
- mpv playback can otherwise reuse a single window: selecting a new channel loads it into the same mpv instance instead of opening a new one. With `python-mpv` installed, Ctrl+Right / Ctrl+Left zap to the next/previous channel **even while that mpv window has keyboard focus** — mpv itself intercepts the keys and calls back into the app, since Qt shortcuts alone only fire while the dopeIPTV window is focused. VLC and "open externally" still spawn a normal one-off process/window. Pick the mode under Settings → "Playback (mpv)".
- Channel logos loaded asynchronously and cached
- Proper application name and icon in the taskbar (instead of "python3")
- Copy stream URL via right-click; "open externally in mpv/VLC" also available from the same menu
- Choose default player, live stream format (ts / m3u8), and whether mpv reuses its window in Settings
- Finds VLC on macOS even when it's only installed as an app bundle (not on `PATH`)

## Installation

```bash
# Dependencies
sudo apt install python3 python3-pip mpv vlc      # Debian/Ubuntu
# or: sudo dnf install python3 mpv vlc             # Fedora
# or: sudo pacman -S python mpv vlc                # Arch
# or on macOS: brew install mpv && brew install --cask vlc

pip install PyQt6 requests

# Optional but recommended on Linux - enables embedded in-app video:
pip install python-mpv
```

> **Embedded playback platform notes:** in-app video uses mpv's window
> embedding, which requires X11 — on Wayland sessions the app automatically
> runs under XWayland when libmpv is available. Embedding relies on X11
> window reparenting, which some Wayland compositors (notably GNOME/Mutter)
> don't propagate correctly for XWayland clients: mpv reports success, but
> the video renders as its own floating window instead of inside the app.
> Because this can't be reliably detected, dopeIPTV defaults to the reused
> **window** mode whenever it had to force XWayland itself, and only
> defaults to **embedded** on native X11 sessions — you can still pick
> "Embedded (in app)" manually in Settings if your compositor happens to
> handle it correctly. On macOS, `wid` embedding isn't supported by libmpv
> at all; playback there always uses the reused external mpv window.

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
| Fullscreen (embedded video) | Double-click the video, or press `F` — `Esc` to exit |
| Zap to next/previous channel | Ctrl+Right / Ctrl+Left |
| Switch player temporarily | Right-click a row |
| Open a series | Double-click the series → episode list appears |
| Search | Type in the search field at the top |
| Refresh EPG | The "Refresh EPG" button in the detail panel |
| Browse the full TV guide | "EPG Guide" button in the sidebar |
| Add a favorite | Right-click a channel → "Add to favorites group" |
| Remove a favorite / group | In Favorites: right-click the channel or the group |
| Remove / clear history | In History: right-click an entry, or "Clear history" |
| Switch account | Settings → "Switch account / server" |

## Troubleshooting

- **"Player not found"** — install mpv or VLC (see above). On macOS, VLC is detected even if it's only in `/Applications` and not on `PATH`.
- **Live stream won't start** — try switching the live format to `m3u8` in Settings.
- **No categories** — check the server URL (include the port, e.g. `:8080`).
- **No EPG for a channel** — the provider may not have a programme guide mapped to it at all; dopeIPTV already tries three different sources (short EPG, full EPG table, XMLTV) before giving up.
