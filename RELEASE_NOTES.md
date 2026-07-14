## dopeIPTV 0.7.0

The **timeshift & catch-up** release: scrub back into a channel's archive on a
live timeline, **pause live TV**, or jump to a **specific past programme** — plus
a **reminders manager**, **search everywhere**, **customizable keyboard
shortcuts**, and **rock-solid video** by default.

**Highlights:** **live timeline** to scrub the archive · **DVR pause** · **play a specific past programme** · **archive-depth learning** · **reminders manager** · **category & left-column search** · **customizable keyboard shortcuts** · **software decode by default** (hardware opt-in) · **editable recordings**.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Timeshift, catch-up & DVR
- **Live timeline** on timeshift channels — scrub back into the provider's archive and watch what already aired.
- **DVR-style pause** — pause live TV and resume behind live; the player shows how far behind live you are and drops the LIVE tag the moment you pause.
- **Pick a specific past programme** from the guide and play just that show, with a seek bar clamped to the programme's own length.
- **Archive-depth learning** — the app learns how far back each channel really reaches, adapts the “go back” span to it, and recovers gracefully from over-deep requests instead of dropping the channel.
- **Catch-up from History**, a **Go-live** button, amber timeshift markers, and arrow-key scrubbing (fine-seek inside a segment, Shift+arrow for a coarse step).
- **Per-channel reset** — right-click a timeshift channel to clear its learned archive state.

### Reminders & guide
- **Reminders manager** — find, review and multi-delete programme reminders in one place.
- **Programme search across the whole guide** — find a show by name anywhere in the EPG.
- **Configurable “upcoming programmes”** in the detail pane (default 5, adjustable), plus a guide **Close** button and deeper EPG fetches.

### Search everywhere
- **Category search** — match category names *and* the channels inside them, with smart ranking.
- **Left-column search** in Favorites, Watch Later, Watched, Recordings and History.
- **Jump-to-now-playing** also selects the playing channel's category.

### Keyboard & navigation
- **Customizable keyboard-shortcuts editor** with sensible per-OS defaults.

### Video & playback
- **Software decoding is now the default** — like standalone mpv. Modern CPUs handle even 4K 10-bit HEVC/HDR comfortably, and it's immune to the GPU/driver render hazards that could black out hardware-decoded video with subtitles (e.g. the nvidia-open stack).
- **Hardware decoding is an opt-in** setting (Settings → Playback → Video), with mpv's software fallback as a safety net for genuine decoder failures.
- **Video filters** — optional deinterlace, sharpen and HDR tone-mapping, plus a **Video** menu in the in-player options.
- Faster channel switching via a light stream probe for live zapping.

### Recordings
- **Editable recording title & description**, and recording **stops cleanly when you switch** the channel it's capturing.

### Settings & polish
- Playback settings are **grouped and scrollable**, and **scrolling the page never changes a control**.
- **Themed spin boxes** (fixes the white-on-white “upcoming count” box) and a tidy **Maintenance** row.
- **Fixed an exit segfault** on newer Python; on **macOS**, arrow-key scrubbing works and Picture-in-Picture stays floating; audio no longer goes silent after switching a movie to a TV channel.
</details>

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
