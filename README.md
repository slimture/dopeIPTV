# dopeIPTV

An elegant IPTV client for Linux with a modern dark interface, supporting the **Xtream Codes API**, **EPG**, and playback via **mpv** or **VLC**.

## Features

- TV, Movies, and Series with favorites, history, and fast search
- Embedded in-app video via libmpv OpenGL — works on any compositor (GNOME, KDE, Hyprland, ...)
- Full EPG with progress bars, guide dialog, and persistent disk cache
- Recording with timers, size caps, and folder management
- Timeshift / catch-up for archived channels
- Themes (5 palettes + 7 accent colors), parental control, Chromecast
- Multiple playlists, content manager, editable channel lists

See [FEATURES.md](FEATURES.md) for the full feature list.

## Installation

Download [AppImage](https://github.com/slimture/dopeIPTV/releases/download/0.1.1-stable/dopeIPTV-x86_64.AppImage) or follow instructions below:

Make sure Git is installed:

```bash
# Debian/Ubuntu
sudo apt install git

# Fedora
sudo dnf install git

# Arch
sudo pacman -S git
```

Install using install.sh:

```bash
git clone https://github.com/slimture/dopeIPTV.git
cd dopeIPTV
./install.sh
```

The installer will install the required dependencies:

```bash
# Dependencies (ffmpeg is optional - preferred recorder for the Record feature)
sudo apt install python3 python3-pip pipx mpv vlc ffmpeg   # Debian/Ubuntu
# or: sudo dnf install python3 python3-pip pipx mpv vlc ffmpeg  # Fedora
# or: sudo pacman -S python python-pipx mpv vlc ffmpeg          # Arch

pipx ensurepath
pipx install .
```

## Running

```bash
dopeiptv
```

The first time, enter your Xtream server (e.g. `http://server:8080`), username, and password.

## Add to the application menu (optional)

```bash
mkdir -p ~/.local/share/applications
cp dopeiptv.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications
```

The application icon is installed automatically to `~/.local/share/icons` the first time the app starts.

## Usage

| Action | How |
|---|---|
| Play | Double-click, or the "Play in mpv / VLC" buttons |
| Pause / skip / volume | ⏸, −10s / +30s, and the volume slider + mute in the player bar |
| Audio track / subtitles / aspect / buffer | The ⚙ button in the player bar (also in fullscreen) |
| Stats for nerds | ⚙ → Stats for nerds (live overlay on the video) |
| Fullscreen | Double-click the video, or press `F` — `Esc` to leave |
| Zap to next/previous channel | Ctrl+Right / Ctrl+Left |
| Rename / hide a channel | Right-click it |
| Record a channel | Right-click → Record (or the REC button in the player) |
| Stop a recording | Click the red ● REC indicator under the list |
| Change theme / accent color | Settings → Interface |
| Watch a programme from the start | Right-click a ⏪ channel → Timeshift |
| Search | Type in the search field at the top |
| Refresh EPG | The "Refresh EPG" button in the detail panel |
| Switch account | Settings → "Switch account / server" |

## Troubleshooting

- **"Player not found"** — install mpv or VLC (see above).
- **Live stream won't start** — try switching the live format to `m3u8` in Settings.
- **No categories** — check the server URL (include the port, e.g. `:8080`).
- **No EPG for a channel** — the provider may not have a programme guide mapped to it; dopeIPTV tries three different sources (short EPG, full EPG table, XMLTV) before giving up.
