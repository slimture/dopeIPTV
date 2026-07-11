# Changelog

All notable changes to dopeIPTV, newest first. This project loosely follows
[Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/). Each release is also published, with
downloads, on the [GitHub releases page](https://github.com/slimture/dopeIPTV/releases).

## [0.6.4]

Stability &amp; housekeeping: **4K plays smoothly**, the app **no longer bloats or
slows down over time**, and **live TV recovers from drops on its own** — plus
continue watching, EPG reminders, and a discreet update check.

<details>
<summary><b>Full notes</b></summary>

### Smooth 4K &amp; playback stability
- **Fixed the periodic 4K stutter.** The resume‑position save was rewriting the whole multi‑MB settings file every 12 s — it now lives in its own small file, so playback is never hitched by it.
- **Fixed the video stutter when dragging the volume slider** (and other quick settings): the big TMDB/Trakt caches shared that same file, so they've moved to a dedicated `cache.ini` and small writes are instant.
- **Hardware decoding on by default** (`hwdec=auto-copy-safe`) for smooth 4K across GPUs, with `DOPEIPTV_HWDEC` / `DOPEIPTV_DEMUX_MAX` / `DOPEIPTV_VIDEO_SYNC` escape hatches.
- Audio no longer goes silent after switching a movie to a TV channel.

### No bloat or slow‑down over time
- **TMDB caches are now capped** so months of browsing can't grow them without bound.
- **EPG guides are gzip‑compressed on disk** (hundreds of MB → tens) — raw guide and parsed index.
- **Orphaned EPG caches are cleaned up at startup** — guides for playlists you've removed no longer pile up (can reclaim gigabytes).
- New **Settings → Playback → “Refresh guide now”** and **“Clear EPG cache”**.

### Live TV
- **Live streams reconnect on drop** instead of freezing on the last frame.
- **“Auto‑reconnect live streams” toggle** — turn it off on a single‑connection/shared account so the app doesn't grab the connection back from another device.
- **Zap conveniences:** a “last channel” key and type‑a‑number to jump.
- **Double‑click to play a channel** by default (single‑click / arrow‑key zapping is now an opt‑in setting).
- **Account status panel:** expiry, days left, and connections for the selected provider.

### Continue watching &amp; Recently added
- **Continue watching** for partly‑watched movies **and** episodes, with the series' artwork + name on episode rows — under both Movies and Series.
- **Recently added** — newest Movies and Series first.

### EPG &amp; reminders
- **Programme reminders** — get notified when a show starts, then tune straight in.
- Clearer loading: a centred spinner and a label that names what's loading.

### Player extras
- **Next‑episode button** and **auto‑play the next episode** at the end.
- **Sleep timer** — stop playback after a chosen number of minutes (presets or custom).
- Stats‑for‑nerds fixes (no more blank rows).

### Trakt &amp; Settings
- Simpler Trakt connect (connect via browser).
- Watched rows are named from Trakt's own title when TMDB has no match.

### Polish &amp; fixes
- **Discreet update indicator** — a small “Update available” pill in the sidebar when a newer release is out (once‑a‑day background check; opt out in Settings → Interface).
- Sidebar rail collapses/expands smoothly within a single drag and snaps clean on release.
- Version numbers realigned, a latent startup crash fixed, and debug scaffolding removed.
</details>

## [0.6.3]

A stability‑and‑polish release: **keyboard shortcuts** across the player and EPG
guide, a **calmer progress indicator**, and a batch of **playback stability**
fixes — plus the app is now **Flathub‑ready** (fully offline build).

<details>
<summary><b>Full notes</b></summary>

### Keyboard shortcuts
- **Global:** `Ctrl+G` opens the EPG guide, `Ctrl+B` toggles the sidebar rail, `Ctrl+Shift+M` toggles focus mode.
- **While the player is up:** `M` mute, `P` Picture‑in‑Picture, `R` record, `I` stream stats, `←`/`→` zap channels, `↑`/`↓` nudge the volume.
- **In the EPG grid:** `N` jumps to now, `P` to the playing channel, `Enter` plays the selected programme.

### Progress indicator
- The loading strip is now a **steady indeterminate indicator** instead of a percentage bar that could stick at “100%”.
- A **watchdog** clears it automatically if a background job never reports back.

### Playback stability
- **Auto‑reconnect** — a live stream that drops on a network hiccup retries quietly instead of failing.
- **Stall recovery** — a watchdog notices a frozen picture and recovers it.
- **No drop on window move** — moving/reparenting the window no longer tears down and rebuilds the video.

### Packaging
- **Flathub‑ready** — the Flatpak manifest builds **fully offline** from pinned, vendored wheels, plus a submission guide.
- Fresh **store screenshots**.
</details>

## Earlier releases

Notes for **0.6.2** and earlier are on the
[GitHub releases page](https://github.com/slimture/dopeIPTV/releases).

[0.6.4]: https://github.com/slimture/dopeIPTV/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/slimture/dopeIPTV/compare/v0.6.2...v0.6.3
