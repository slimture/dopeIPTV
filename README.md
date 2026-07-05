# dopeIPTV

An elegant IPTV client for Linux with a macOS-inspired dark interface, supporting the **Xtream Codes API**, **EPG**, and playback via **mpv** or **VLC**.

## Features

- Log in with Xtream Codes credentials (server, username, password) — settings are saved
- Live TV, Movies (VOD), and Series with seasons and episodes
- Categories in the sidebar and fast search
- EPG: "Now playing" with progress bar + upcoming programs for the selected channel
- Refresh the EPG with one click (↻) — it also refreshes automatically when the current program ends
- Movie and series details in the detail panel: plot, genre, cast, director, release date, and rating
- Clear message when a channel has no EPG data (instead of an empty panel)
- Channel logos loaded asynchronously
- Proper application name and icon in the taskbar (instead of "python3")
- Play in mpv or VLC (double-click, buttons, or right-click menu)
- Copy stream URL via right-click
- Choose default player and live stream format (ts / m3u8) in Settings

## Installation

```bash
# Dependencies
sudo apt install python3 python3-pip mpv vlc      # Debian/Ubuntu
# or: sudo dnf install python3 mpv vlc             # Fedora
# or: sudo pacman -S python mpv vlc                # Arch

pip install PyQt6 requests
```

## Running

```bash
python3 dopeiptv.py
```

The first time, enter your Xtream server (e.g. `http://server:8080`), username, and password.

## Add to the application menu (optional)

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
| Switch player temporarily | Right-click a row |
| Open a series | Double-click the series → episode list appears |
| Search | Type in the search field at the top |
| Refresh EPG | The "↻ Uppdatera EPG" button in the detail panel |
| Switch account | Settings → "Byt konto / server" |

## Troubleshooting

- **"Player missing"** — install mpv or VLC (see above).
- **Live stream won't start** — try switching the live format to `m3u8` in Settings.
- **No categories** — check the server URL (include the port, e.g. `:8080`).
