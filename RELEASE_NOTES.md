## dopeIPTV 0.6.3

A stability-and-polish release: **keyboard shortcuts** across the player and
EPG guide, a **calmer progress indicator**, and a batch of **playback
stability** fixes — live streams reconnect on their own, a frozen picture
recovers, and moving the window no longer drops the stream. Behind the scenes
the app is now **Flathub-ready** (fully offline build) with fresh store
screenshots.

**Highlights:** **keyboard shortcuts** (player + EPG) · steadier **progress indicator** · **auto-reconnect** & **stall recovery** for live streams · no stream drop when moving the window · **Flathub** offline packaging.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Keyboard shortcuts
- **Global:** `Ctrl+G` opens the EPG guide, `Ctrl+B` toggles the sidebar rail, `Ctrl+Shift+M` toggles focus mode.
- **While the player is up:** `M` mute, `P` Picture-in-Picture, `R` record, `I` stream stats, `←`/`→` zap channels, `↑`/`↓` nudge the volume. Shortcuts stand down while you're typing in a search box.
- **In the EPG grid:** `N` jumps to now, `P` to the playing channel, `Enter` plays the selected programme.
- The full list lives in the **Keyboard shortcuts** section of the README.

### Progress indicator
- The loading strip is now a **steady indeterminate indicator** instead of a percentage bar that could stick at "100%" or hang around after a load finished.
- A **watchdog** clears it automatically if a background job never reports back, so it can't get stuck on screen.

### Playback stability
- **Auto-reconnect** — a live stream that drops on a network hiccup now retries quietly and keeps playing, instead of failing with "loading failed".
- **Stall recovery** — a watchdog notices a frozen picture (mpv idling mid-stream) and recovers it.
- **No drop on window move** — moving or reparenting the window (e.g. dragging between monitors) no longer tears down and rebuilds the video, which previously showed up as a stream failure.
- Longer network timeouts and mpv reconnect options for flakier providers.

### Packaging
- **Flathub-ready** — the Flatpak manifest now builds **fully offline** from pinned, vendored Python wheels (only the running app needs the network, for streaming), plus a submission guide in `packaging/flatpak/FLATHUB.md`.
- Fresh **store screenshots** and an updated capture guide.

</details>

**Full changelog:** https://github.com/slimture/dopeIPTV/compare/v0.6.2...v0.6.3
