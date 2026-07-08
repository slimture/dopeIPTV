# dopeIPTV

An elegant IPTV client for Linux with a modern dark interface, supporting the
**Xtream Codes API**, **EPG**, and an **embedded mpv** video player.

## Features

- TV, Movies, and Series with favorites, history, and fast search
- Embedded in-app video via libmpv OpenGL — works on any compositor (GNOME, KDE, Hyprland, ...)
- Full EPG with progress bars, guide dialog, and persistent disk cache
- Recording with timers, size caps, and folder management (uses ffmpeg)
- Timeshift / catch-up for archived channels
- TMDB artwork/metadata and Trakt scrobbling (optional, configured in Settings)
- Themes (5 palettes + 7 accent colors), parental control, Chromecast
- Multiple playlists, content manager, editable channel lists

See [FEATURES.md](FEATURES.md) for the full feature list.

## Dependencies

dopeIPTV's embedded player is built around **libmpv** and its Record/Cast
features around **ffmpeg** and **pychromecast**, so these are required, not
optional:

| Component | Provides | Required |
|---|---|---|
| `python3` (≥ 3.11) | runtime | ✅ |
| `mpv` / `libmpv` | embedded video playback | ✅ |
| `python-mpv` | Python binding to libmpv | ✅ (pip) |
| `ffmpeg` | recording backend | ✅ |
| `pychromecast` | Chromecast casting | ✅ (pip) |
| `vlc` | optional *external* player only | ⬜ |

The Python packages (`PyQt6`, `requests`, `python-mpv`, `pychromecast`) are
installed automatically by pip/pipx from `requirements.txt`. The system
packages (`mpv`, `ffmpeg`, and optionally `vlc`) come from your distro.

## Installation

### Option A — AppImage (easiest, no dependencies to install)

Download the latest `dopeIPTV-x86_64.AppImage` from the
[Releases page](https://github.com/slimture/dopeIPTV/releases), then:

```bash
chmod +x dopeIPTV-*.AppImage
./dopeIPTV-*.AppImage
```

The AppImage bundles Python, PyQt6, libmpv, and ffmpeg — nothing else to
install. (To integrate it into your application menu, use a tool like
[Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) or AppImageLauncher.)

### Option B — install from source with pipx

```bash
git clone https://github.com/slimture/dopeIPTV.git
cd dopeIPTV
./install.sh
```

`install.sh` detects your package manager (apt / dnf / pacman), installs the
system dependencies, then installs dopeIPTV with `pipx`.

To do it manually instead:

```bash
# Debian/Ubuntu
sudo apt install python3 python3-pip pipx mpv ffmpeg   # add vlc for external playback
# Fedora
sudo dnf install python3 python3-pip pipx mpv ffmpeg
# Arch
sudo pacman -S python python-pipx mpv ffmpeg

pipx ensurepath
pipx install .
```

## Running

```bash
dopeiptv
```

The first time, enter your Xtream server (e.g. `http://server:8080`),
username, and password.

## Add to the application menu (optional)

```bash
mkdir -p ~/.local/share/applications
cp dopeiptv.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications
```

The application icon is installed automatically to `~/.local/share/icons`
the first time the app starts.

## Usage

| Action | How |
|---|---|
| Play | Double-click, or the **▶ Play** button in the detail panel |
| Pause / skip / volume | ⏸, −10s / +30s, and the volume slider + mute in the player bar |
| Audio track / subtitles / aspect / buffer | The ⚙ button in the player bar (also in fullscreen) |
| Stats for nerds | ⚙ → Stats for nerds (live overlay on the video) |
| Fullscreen | Double-click the video, or press `F` — `Esc` to leave |
| Picture-in-Picture | The **PiP** button in the player bar |
| Zap to next/previous channel | Ctrl+Right / Ctrl+Left |
| Rename / hide a channel | Right-click it |
| Open in an external player (mpv/VLC) | Right-click → Open externally |
| Record a channel | Right-click → Record (or the ● REC button in the player) |
| Stop a recording | Click the red ● REC indicator under the list |
| Change theme / accent color | Settings → Interface |
| Watch a programme from the start | Right-click a ⏪ channel → Timeshift |
| Browse the full guide | The **EPG Guide** button in the sidebar |
| Search | Type in the search field at the top |
| Switch account | Settings → "Switch account / server" |

## Troubleshooting

- **Embedded playback disabled** — install `mpv`/`libmpv` and `python-mpv` (see Dependencies).
- **Recording does nothing** — install `ffmpeg`; it is the recording backend.
- **Live stream won't start** — try switching the live format to `m3u8` in Settings.
- **No categories** — check the server URL (include the port, e.g. `:8080`).
- **No EPG for a channel** — the provider may not have a programme guide mapped to it; dopeIPTV tries three different sources (short EPG, full EPG table, XMLTV) before giving up.
