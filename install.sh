#!/bin/bash

sudo pacman -S --needed python python-pipx mpv vlc ffmpeg

pipx ensurepath

pipx install .

mkdir -p ~/.local/share/applications
cp dopeiptv.desktop ~/.local/share/applications/

update-desktop-database ~/.local/share/applications
