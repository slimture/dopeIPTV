## dopeIPTV 0.6.0

A big feature release focused on getting started, Trakt, the EPG, and polish.

### Getting started
- **Try it without a provider** — a "🎬 Demo channels" button on the welcome
  screen loads a few free public test streams so you can exercise the whole
  app before entering any credentials.
- **M3U playlists** — alongside Xtream Codes you can now add any free/legal
  `.m3u` playlist URL (e.g. iptv-org). Pick the type in the playlist dialog or
  the onboarding wizard.
- **Onboarding wizard** — full in-window welcome with language pick, a short
  feature tour, and Esc to dismiss.

### Trakt — one click, zero setup
- **Sign in with Trakt** now opens your browser, you click "Yes", and you're
  connected — no codes, no manual API keys (dopeIPTV ships its own registered
  app; power users can still bring their own in Settings).

### Watched
- Movies and episodes are **auto-marked watched** once you've seen ~90%, in
  the local layer — the badge appears without Trakt, and syncs up to Trakt
  when connected.

### Favorites
- **Unified folders** across Channels, Movies and Series: a plain
  "Add to favorites", an "Add to folder" submenu, and create/rename/remove
  folders from the sidebar's right-click menu.

### EPG
- **New horizontal timeline grid**: colour-coded channel rows, programmes as
  blocks, a live "now" line, and pinned time header + channel column. Click to
  select, double-click to play, right-click to record a programme.
- Timeshift channels open the board up to 48h **into the past** with **catch-up
  playback** of finished programmes; a "Now" button jumps back to the present.
- From Favorites the guide is scoped to your favorite channels.

### Player & windows
- **Adaptive first-run window sizing** to your actual display; a larger,
  letterbox-free mini player that scales with the screen.
- **Picture-in-Picture always-on-top** on Wayland (via the title-bar menu),
  plus an opt-in "Run via X11 backend" for a frameless, always-on-top PiP with
  right-click-on-video.
- Opening an **external player** (mpv/VLC) now asks first if the mini player is
  busy, so it won't trip single-connection accounts.

### Other
- **About** dialog: version, an automatic update check that shows the newest
  release's notes and a download link, and TMDB attribution.
- **Recordings**: a total recordings-folder size cap that refuses new
  recordings once reached.
- Tighter section headers, clearer EPG scrollbars, and lower memory use
  (bounded metadata/artwork caches).
