## dopeIPTV 0.6.0

A big feature release focused on getting started, Trakt, and the EPG.

**Highlights:** one-click **Sign in with Trakt** · auto-mark **watched** · **demo channels** + **M3U** playlists · unified **favorite folders** · a new horizontal **EPG timeline grid** with catch-up · adaptive window sizing · in-app **update check**.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Getting started
- **Try it without a provider** — a "🎬 Demo channels" button loads a few free public test streams so you can exercise the whole app before entering any credentials.
- **M3U playlists** — alongside Xtream Codes you can add any free/legal `.m3u` playlist URL (e.g. iptv-org). Choose the type in the playlist dialog or the onboarding wizard.
- **Onboarding wizard** — full in-window welcome with language pick, a short feature tour, and Esc to dismiss.

### Trakt — one click, zero setup
- **Sign in with Trakt** opens your browser, you click "Yes", and you're connected — no codes, no manual API keys (power users can still bring their own in Settings).

### Watched
- Movies and episodes are **auto-marked watched** once you've seen ~90%, locally — the badge appears without Trakt and syncs up when connected.

### Favorites
- **Unified folders** across Channels, Movies and Series: a plain "Add to favorites", an "Add to folder" submenu, and create/rename/remove folders from the sidebar's right-click menu.

### EPG
- **New horizontal timeline grid**: colour-coded channel rows, programmes as blocks, a live "now" line, and a pinned time header + channel column. Click to select, double-click to play, right-click to record a programme.
- Timeshift channels open the board up to 48h **into the past** with **catch-up playback**; a "Now" button jumps back to the present.
- From Favorites the guide is scoped to your favorite channels.

### Player & windows
- **Adaptive first-run window sizing** to your display, and a larger, letterbox-free mini player.
- **Picture-in-Picture always-on-top** on Wayland (title-bar menu), plus an opt-in "Run via X11 backend" for a frameless, always-on-top PiP.
- Opening an **external player** now asks first if the mini player is busy, so it won't trip single-connection accounts.

### Other
- **About** dialog: version, an automatic update check with the latest release's notes and a download link, and TMDB attribution.
- **Recordings**: a total recordings-folder size cap.
- Tighter section headers, clearer EPG scrollbars, and lower memory use.

</details>

**Full changelog:** https://github.com/slimture/dopeIPTV/compare/v0.5.0...v0.6.0
