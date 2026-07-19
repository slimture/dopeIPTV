## dopeIPTV 0.9.0

**Home** — a full-window start page — plus an interactive EPG guide,
reminders & recording for upcoming programmes, and a big round of player,
artwork and stability work.

- **Home**: a Featured hero row and shelves for Continue watching,
  Favourites now, favourite movies & series, Recently viewed and Recently
  added movies, series *and* TV channels. Instant cold-start posters via a
  per-playlist disk cache. Right-click a channel tile to set a reminder or
  record. Every shelf is configurable in Settings.
- **EPG guide, interactive**: logos, descriptions, progress, day-jumps,
  arrow-key navigation, sticky titles — and it follows your accent colour.
- **Upcoming programmes**: when a stream hasn't started, get the answer at
  once and set a **reminder** or a **recording** (until it ends, or a custom
  length) — from the prompt, the channel list, Home or the programme guide.
- **Player**: click the video to pause/play with a centred play/pause disc
  (docked, fullscreen, pop-out); complete right-click menus everywhere;
  the timeshift timeline shows the target time on hover.
- **Artwork**: episode posters via the show's TMDB art, junk cover URLs
  skipped, recordings show the channel logo.
- **Snappier**: definitive stream errors (forbidden / not found / not
  started) surface in about a second; "Recently added" is cached; many
  macOS repaint fixes and 246 tests green.
- **Instant startup, outage-proof**: the app opens immediately (credentials
  verify in the background), lists are cached to disk per playlist so a
  down provider still shows the last known lineup, and after a network
  failure the client fails fast for 30 s instead of hanging for minutes.
- **Timeshift you can trust**: only a proven provider answer can hide a
  channel's catch-up - transient errors and early not-seekable readings
  no longer silently strip timeshift off working channels.

**Note:** the first launch of 0.9.0 resets stored settings to the new
defaults (too much changed to carry the old ones). Playlists, favorites,
history, reminders, recordings and your Trakt account are all kept.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target - Windows and macOS are a bonus.
