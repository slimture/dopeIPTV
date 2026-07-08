#!/usr/bin/env bash
# dopeIPTV installer: install system dependencies for the distro we're on,
# then install the app itself with pipx.
#
#   mpv/libmpv  -> embedded player   (required)
#   ffmpeg      -> recording backend (required)
#   vlc         -> external player   (optional; pass --with-vlc to include)
set -euo pipefail

WITH_VLC=0
for arg in "$@"; do
    [ "$arg" = "--with-vlc" ] && WITH_VLC=1
done

msg() { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
err() { printf '\033[1;31mError:\033[0m %s\n' "$1" >&2; exit 1; }

if command -v apt-get >/dev/null 2>&1; then
    msg "Detected apt (Debian/Ubuntu)"
    pkgs=(python3 python3-pip pipx mpv libmpv2 ffmpeg)
    [ "$WITH_VLC" = 1 ] && pkgs+=(vlc)
    sudo apt-get update
    # libmpv2 is named libmpv1/libmpv-dev on older releases; fall back gracefully.
    sudo apt-get install -y "${pkgs[@]}" \
        || sudo apt-get install -y python3 python3-pip pipx mpv ffmpeg
elif command -v dnf >/dev/null 2>&1; then
    msg "Detected dnf (Fedora)"
    pkgs=(python3 python3-pip pipx mpv mpv-libs ffmpeg)
    [ "$WITH_VLC" = 1 ] && pkgs+=(vlc)
    sudo dnf install -y "${pkgs[@]}"
elif command -v pacman >/dev/null 2>&1; then
    msg "Detected pacman (Arch)"
    pkgs=(python python-pipx mpv ffmpeg)
    [ "$WITH_VLC" = 1 ] && pkgs+=(vlc)
    sudo pacman -S --needed "${pkgs[@]}"
elif command -v zypper >/dev/null 2>&1; then
    msg "Detected zypper (openSUSE)"
    pkgs=(python3 python3-pipx mpv ffmpeg)
    [ "$WITH_VLC" = 1 ] && pkgs+=(vlc)
    sudo zypper install -y "${pkgs[@]}"
else
    err "No supported package manager found (apt/dnf/pacman/zypper). Install mpv, libmpv, ffmpeg and pipx manually, then run: pipx install ."
fi

msg "Installing dopeIPTV with pipx"
pipx ensurepath
pipx install --force .

msg "Adding the desktop entry"
mkdir -p ~/.local/share/applications
cp dopeiptv.desktop ~/.local/share/applications/
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database ~/.local/share/applications || true

msg "Done. Launch it with:  dopeiptv"
