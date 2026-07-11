## dopeIPTV 0.6.4

A stability-and-housekeeping release: **4K plays smoothly**, the app **no longer
bloats or slows down over time**, and **live TV recovers from drops on its own**
— plus **continue watching**, **EPG reminders**, and a **discreet update check**.

**Highlights:** smooth **4K playback** · caches that **can't bloat over time** · **live auto-reconnect** (with a toggle) · **double-click** to play TV · **continue watching** & **reminders** · discreet **update badge**.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Smooth 4K & playback stability
- **Fixed the periodic 4K stutter.** The resume-position save was rewriting the whole multi-MB settings file every 12 s — it now lives in its own small file, so playback is never hitched by it.
- **Fixed the video stutter when dragging the volume slider** (and other quick settings): the big TMDB/Trakt caches shared that same file, so they've moved to a dedicated `cache.ini` and small writes are instant.
- **Hardware decoding on by default** (`hwdec=auto-copy-safe`) for smooth 4K across GPUs, with `DOPEIPTV_HWDEC` / `DOPEIPTV_DEMUX_MAX` / `DOPEIPTV_VIDEO_SYNC` escape hatches for tinkerers.
- Audio no longer goes silent after switching a movie to a TV channel (the audio track is reset per stream).

### No bloat or slow-down over time
- **TMDB caches are now capped** so months of browsing can't grow them without bound.
- **EPG guides are gzip-compressed on disk** (hundreds of MB → tens) — both the raw guide and the parsed index.
- **Orphaned EPG caches are cleaned up at startup** — guides for playlists you've removed no longer pile up (this alone can reclaim gigabytes).
- New **Settings → Playback → "Refresh guide now"** and **"Clear EPG cache"**.

### Live TV
- **Live streams reconnect on drop** instead of freezing on the last frame.
- **"Auto-reconnect live streams" toggle** — turn it off on a single-connection/shared account so the app doesn't grab the connection back from another device.
- **Zap conveniences:** a "last channel" key and type-a-number to jump.
- **Double-click to play a channel** by default (single-click / arrow-key zapping is now an opt-in setting).
- **Account status panel:** expiry, days left, and connections for the selected provider.

### Continue watching & Recently added
- **Continue watching** for partly-watched movies **and** episodes, with the series' artwork + name on episode rows — shown under both Movies and Series.
- **Recently added** — newest Movies and Series first.

### EPG & reminders
- **Programme reminders** — get notified when a show starts, then tune straight in.
- Clearer loading: a centred spinner and a label that names exactly what's loading.

### Player extras
- **Next-episode button** and **auto-play the next episode** at the end.
- **Sleep timer** — stop playback after a chosen number of minutes (presets or a custom value).
- Stats-for-nerds fixes (no more blank rows).

### Trakt & Settings
- Simpler Trakt connect (connect via browser).
- Watched rows are named from Trakt's own title when TMDB has no match.

### Polish & fixes
- **Discreet update badge** on the Settings button when a newer release is out (once-a-day background check; opt out in Settings → Interface).
- Sidebar rail collapses/expands smoothly within a single drag and snaps clean on release.
- Version numbers realigned, a latent startup crash fixed, and debug scaffolding removed.

</details>

**Full changelog:** https://github.com/slimture/dopeIPTV/compare/v0.6.3...v0.6.4
