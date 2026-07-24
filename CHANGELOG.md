# Changelog

All notable changes to dopeIPTV, newest first. This project loosely follows
[Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/). Each release is also published, with
downloads, on the [GitHub releases page](https://github.com/slimture/dopeIPTV/releases).

## [1.2.1]

Clearer update notices, a readable About dialog, and a calmer default startup.

- **A clear "new version available" banner.** When a newer release is found, a
  dismissible banner now appears across the top of the window - visible on every
  view, including the full-window Home page where the old sidebar badge was easy
  to miss. It shows once per version (and stays gone once dismissed).
- **Download goes to the website.** The banner's and the About dialog's
  **Download** buttons now open the website instead of the raw GitHub release.
- **Fixed: the About dialog's release notes were unreadable** - an unthemed
  white box with faint text. The notes panel now uses the app theme.
- **The app starts on TV by default.** Home is now opt-in: turn on "Open Home on
  startup" in Settings to land there instead. This applies to first run too.

## [1.2.0]

A dedicated place to manage upcoming recordings, a smoother maximize on macOS,
and a batch of pop-out and Home polish.

### Recordings

- **New "Scheduled recordings" panel.** A dedicated window lists every pending
  recording (recording now + scheduled), soonest first, with **Edit start/stop
  time** and **Cancel** right there. Open it from the player's REC menu or a new
  button in the EPG guide, so upcoming recordings are managed in one place
  instead of hopping between the sidebar and the middle column. It refreshes
  live as recordings start, finish or get cancelled.
- **Upcoming recordings surface on the default Recordings view.** The
  "All recordings" view now shows the pending recordings under an "Upcoming"
  header at the top (they were previously only under the "Active & scheduled" /
  "Upcoming" sub-categories). Folder views are unaffected.
- **Fixed: "Watch the recorded channel" did nothing when picked the instant a
  recording started.** ffmpeg creates the output file a beat after the job
  begins, so the option found no file and gave up silently. It now waits
  briefly for the file and starts playback automatically.
- Recording status and category labels are now translated in every language.

### Player & pop-out

- **macOS: maximizing the mini player is now as smooth as the pop-out.** It goes
  fullscreen through a frameless mirror window instead of the decorated main
  window, skipping the slow native fullscreen animation and the pane teardown -
  and you get the full control bar, auto-hiding controls and cursor-hide in
  maximized mode. Every way out (button, Escape, double-click) docks straight
  back to the mini player.
- **The pop-out control bar auto-hides** after a few idle seconds and returns on
  mouse movement, and **the mouse cursor now hides** over the video in the
  pop-out - windowed and fullscreen.
- **Sleep-timer countdown.** A top-right pill shows the time left before
  playback stops. It auto-hides with the other controls but pins on - and turns
  red - for the final 30 seconds, so the imminent stop is unmissable.
- **Fixed: a crash when opening the pop-out.** The seek bar's hover tooltip
  held a reference to a window that had been destroyed by the reparent; it is
  now rebuilt on demand.
- **Windows: the pop-out now uses the same mirror approach as macOS.** This is
  **experimental and not yet sufficiently tested on Windows** - treat Windows
  pop-out as unstable for now. Linux keeps its existing (unchanged) pop-out.

### Home

- **Resuming a movie from Home lands in its own category** with the movie
  selected, instead of leaving you in whatever category was last open.
- TV-channel logos on Home are centred in their tiles (and a touch larger).

### Settings

- **"Reset all settings" now really clears everything.** Watched, Watch Later
  and resume positions live in separate config files and used to survive a
  reset; all three are now cleared alongside the main settings.

## [1.1.0]

Video stability on macOS, smarter series navigation, a leaner Home, and a
faster app all around.

### Video

- **Fixed: pop-out froze or blanked the video (audio kept playing).** Docking
  the player in or out of the pop-out window reparents the video widget. On
  macOS (shared OpenGL contexts) Qt *preserves* the GL context across that
  reparent, so none of the usual rebuild hooks fire - the widget's backing
  framebuffer was still composed against the old window and the screen froze
  on the last frame while mpv kept playing. Only a window resize (e.g.
  fullscreen) recovered it. The player now does the equivalent automatically:
  after every dock/undock the video surface gets a 1-pixel resize nudge that
  rebuilds the backing framebuffer against the new window. Pure widget
  geometry - mpv and the stream are never touched. Where the GL context
  genuinely is recreated (Linux), the render context is rebuilt as before,
  plus a throttled self-heal in the paint path as a safety net.
- **Fixed: entering video fullscreen flashed a half-sized frame.** The window
  went fullscreen *after* painting had resumed, so one frame showed the video
  filling the still-normal-sized window. The whole transition is now painted
  as a single clean cut.

### Series navigation

- **"Now playing" jumps into the right episode.** Clicking the sidebar logo
  while an episode plays now lands inside that series' episode list with the
  playing episode selected - previously an async category reload could bounce
  the view back to "all series" before the drill finished.
- **Home's Continue watching opens the series too.** Clicking a partly-watched
  episode on Home plays it *and* drills the middle column into the series'
  episode list with that episode selected.
- **Back from an episode list lands in the series' own category.** Backing out
  now selects the category the series actually lives in (with the series row
  selected), instead of whatever category happened to be selected before.
- **Stable episode posters on Home.** Continue-watching episode cards resolve
  the series' poster (the same art the episode list shows) instead of
  title-searching the mangled episode name as a movie, which made the poster
  appear and disappear at random.
- **Recently viewed treats episodes as episodes.** History rows for episodes
  now carry their series: replaying one resumes where you left off (it used
  to restart from zero), drills into the series' episode list like Continue
  watching, resolves the series' poster, and reads "Series · S1 E2 - Title"
  instead of a bare "S01 E01". The duplicate rows the old context-less
  fallback created are gone (History also dedupes across int/str id types).

### Home

- **New: a Watch Later shelf.** Movies and shows from your Watch Later list,
  with the same play/drill behaviour as everywhere else; rows the provider
  can't act on are filtered out. Toggleable under Settings > Home.
- **Continue watching and Recently viewed no longer overlap.** A partly-
  watched title lives on Continue watching; Recently viewed shows the rest
  (turn Continue watching off and it shows everything again).
- **The TV guide from Home (or Movies/Series) covers your favorites.** It
  used to open over an arbitrary slice - whatever category the hidden TV
  view had auto-selected, or the first 300 channels of the provider's full
  dump. Now it guides your favorite channels (the lineup you actually
  curated) and falls back to the full lineup when you have none. Opened
  from TV or Favorites it is scoped to the current list, as before.
- **Removed the Featured row.** It was the top 12 of the same recently-added
  movies already shown directly below it - pure duplication, and its oversized
  posters were the slowest part of the page.

### Performance

- **Faster startup**: heavy modules load lazily (~40% off import time), and an
  optional custom allocator (jemalloc/mimalloc) can be enabled with the
  `DOPEIPTV_JEMALLOC` environment variable - off by default, a strict no-op
  unless the library exists.
- **Snappier UI**: fullscreen toggles batch their relayout into one paint,
  the Settings dialog builds ~30% faster, list scrolling and divider drags are
  smoother, and the image caches are bounded (2.5 GB disk budget with
  automatic pruning).
- **Sidebar and layout state survive fullscreen**: the sidebar's expanded/rail
  choice and the middle column's full form are kept across video fullscreen
  round-trips.

### Settings

- **A pasted provider link survives messy input.** Pasting an Xtream/M3U
  link into a server field that already held text (or copying it along with
  a text prefix) used to silently fail the auto-fill; the link is now found
  in the pasted text and the fields fill cleanly.
- **Removed the one-time 0.9.0 settings migration.** Every install that
  needed it has run it; it no longer lurks in the background re-pruning
  settings after a manual reset.
- **Fixed: the language dropdown wouldn't scroll in Settings.** The wheel
  guard that stops the mouse wheel from accidentally changing a setting was
  unknowingly installed on every *scrollbar* too (a scrollbar is a slider to
  Qt), including the one inside the language dropdown's popup - and Qt
  delivers wheel scrolling *through* the scrollbar, so the open list ignored
  the wheel entirely on every platform. Scrollbars are now exempt from the
  guard: the popup (capped at 14 rows, with a visible scrollbar) scrolls
  normally with wheel or trackpad, and every other box still swallows the
  wheel so scrolling the page can't nudge a setting. A restart hint under the
  picker says a language change needs a restart. Release builds also verify
  that all 27 languages load from each packaged artifact (.dmg, Windows zip,
  AppImage/.deb, wheel), so a packaging slip can never silently ship an
  English-only picker again.

## [1.0.1]

Packaging fix for the 1.0 release.

- **Fixed: only English available in the installed apps.** 1.0.0 was the first
  release to ship translations as external `dopeiptv/locale/*.json` files
  rather than inline in the code. In the frozen builds (macOS `.dmg`, Windows
  `.exe`, Linux AppImage/`.deb`) those files weren't located at runtime — on a
  macOS `.app` the code lives in `Contents/Frameworks` while the data lands in
  `Contents/Resources` — so the language picker silently collapsed to English
  only. The locale directory is now found across every packaging layout
  (source, wheel, PyInstaller bundle root, macOS `.app`), and the locale files
  are bundled explicitly in all three PyInstaller specs. The pip/pipx install
  was unaffected. A regression test covers the frozen-bundle lookup.

## [1.0.0]

The 1.0 milestone: dopeIPTV now speaks **27 languages**, sets up from a single
pasted link, scrolls large lineups without lag, and ships a strictly
type-checked logic core with a translator-contribution workflow.

- **27 interface languages** — the whole UI is now translated into 27
  languages (up from 8): added Português, Italiano, Nederlands, Polski,
  Hrvatski, Srpski, Ελληνικά, Türkçe, Українська, Bahasa Indonesia, Tiếng
  Việt, हिन्दी, 日本語, 한국어, Kiswahili, العربية, فارسی, עברית and اردو.
  The four right-to-left languages (Arabic, Persian, Hebrew, Urdu) mirror the
  layout. English is the inline source; every other language is a single
  reviewable `dopeiptv/locale/<code>.json` file, so a native speaker can
  correct one language in isolation.
- **Paste a whole Xtream link** — the onboarding wizard and the Add/Edit
  playlist dialog now accept a full Xtream URL (`get.php`, `player_api.php`,
  or a direct stream URL) pasted into the server field and split it into the
  server / username / password fields for you. Typing the three fields by
  hand still works exactly as before, and the language picker in the welcome
  screen now lists all 27 languages.
- **Smart provider detection** — a pasted link is recognised automatically as
  Xtream or M3U and the wizard/dialog switches mode for you. Xtream is always
  preferred (its API also serves movies, series and EPG), so even a `get.php`
  M3U-export link configures a full Xtream provider.
- **Snappier lists** — channel/movie/series logos and posters are now scaled
  once and cached instead of being re-scaled on every repaint, which removed
  the main source of scroll lag with large lineups. Lists also use smooth
  pixel-granular scrolling and lay huge lineups out in background batches.
  None of this touches the video pipeline.
- **Translator workflow** — a `docs/TRANSLATING.md` guide, a "Translation
  fix" issue template, and a `tools/i18n_status.py` health report (coverage,
  stray keys and placeholder mismatches per language) make it easy for native
  speakers to correct the machine-translated locales.
- **Stricter type gate & docs** (internal) — the mypy type-check now covers
  the whole pure-logic layer (13 modules across `providers/`, `core/` and
  `services/`, up from 3), a few latent type issues were fixed, and every
  module and class now carries a docstring.

## [0.9.0]

**Home** — a full-window start page with a Featured hero row and shelves — plus an
interactive EPG guide, reminders/recording for upcoming programmes, and a big
round of player, artwork and stability work.

- **Home** — a SwipTV-style start page (opens at launch, configurable): an
  oversized **Featured** hero row, and shelves for **Continue watching**,
  **Favourites now** (with the current programme), **favourite movies &
  series**, **Recently viewed** and **Recently added** movies, series *and TV
  channels*. Posters paint instantly on a cold start from a per-playlist disk
  cache and refresh in the background. Text-only rounded quick-nav (TV / EPG
  Guide / Movies / Series / Settings) in the accent colour. Right-click a
  channel tile to set a reminder or record without leaving Home. Clicking a
  channel lands the classic list in that channel's own category with the row
  selected, and the detail panel follows. Every shelf can be toggled in
  Settings.
- **EPG guide, interactive** — channel logos, programme descriptions, an info
  panel, progress on the on-air card, day-jump arrows, full arrow-key
  navigation, sticky programme titles while scrolling, playable rows even for
  channels without guide data, duplicate entries dropped, and the whole guide
  follows the theme's accent colour.
- **Upcoming programmes: reminders & recording** — when a stream hasn't
  started yet (the provider's 407), the app says so at once and offers a
  **reminder when it starts** or a **recording** (until the programme ends, or
  a custom length). The same actions live on right-click — in the channel
  list, in Home and in the detail pane's programme guide. A "don't show
  again" opt-out is resettable in Settings.
- **"Recently added"** — a 🆕 category under TV, Movies and Series (newest
  first, cached briefly so re-opening is instant).
- **Player** — click the video to pause/play with a centred play/pause disc
  (docked, fullscreen and pop-out); the right-click menu is complete
  everywhere (pause/stop, fullscreen, audio/subtitle tracks, delay, aspect,
  filters, sleep timer); hovering the timeshift timeline shows the time you'd
  jump to; the poster overlay tracks pause/play state correctly for
  timeshift and catch-up.
- **Artwork** — episode posters resolve through the show's TMDB artwork
  (providers rarely ship per-episode images), junk cover URLs ("n/A" & co.)
  are skipped, and recordings show their channel's logo.
- **Faster failure answers** — a parallel probe surfaces definitive stream
  errors (forbidden / not found / not started) in about a second instead of
  after the whole retry budget.
- **Onboarding & housekeeping** — clearer first-run flow (playlist name hint,
  Trakt confirmation, icon-only actions), Content Manager lists categories
  alphabetically, community health files (contributing, security policy,
  code of conduct, issue templates).
- **Stability** — many macOS repaint fixes (ghosted shelf titles, menus
  bleeding through the video, double-exposed programme lists), a robustness
  test suite (246 tests), fixed EPG-grid crash on rebuild, and the stuck
  "Loading channels…" indicators are gone for good.
- **Instant startup & outage resilience** — the app opens immediately (no
  more blocking "Connecting to…" splash; credentials verify in the
  background), channel/movie/series lists are cached in memory for 5
  minutes AND mirrored to disk per playlist, so a down or overloaded
  provider shows the last known lineup instead of empty lists. After a
  network failure the client fails fast for 30 s (no more minutes-long
  hangs), and any successful reply lifts the cooldown at once.
- **Timeshift trust** — only a proven provider response ("this is an error
  page, not a stream") may ever hide a channel's catch-up. Player-level
  noise (an mpv error right after the swap, a too-early "not seekable"
  reading) no longer silently strips timeshift off working channels, and
  the archive probe treats a network failure on any URL format as
  inconclusive. The TIMESHIFT badge no longer appears when pausing a
  channel without an archive (that pause rides mpv's local buffer).
- **Fewer wrong prompts** — the "upcoming broadcast" reminder/record dialog
  is no longer shown when an overloaded panel answers 407 for an ordinary
  live channel (the guide-on-air check and the network cooldown gate it).
- **One-time settings reset** — because so much changed, the first launch
  of 0.9.0 resets stored settings to the new defaults. Playlists,
  favorites, history, reminders, recordings, category customisations,
  parental PIN, language and the Trakt account are all kept.

## [0.8.1]

**Fix: working catch-up channels could silently lose their timeshift.**

- A momentary network failure during the archive check (timeout, DNS/TLS error, refused connection) was treated as proof that a channel serves no catch-up, hiding its whole timeshift UI for 14 days with no message. Only a real provider response (an error page instead of a stream) can hide a channel's catch-up now; network failures leave everything untouched and show a clear "couldn't reach the archive" status instead. Channels already hidden on 0.8.0 come back after one playlist refresh (↻).

## [0.8.0]

**Multiview** — watch up to nine live channels at once — plus a sharper,
truly cross-platform interface and a long list of fixes.

- **Multiview** — a separate grid window (2/4/6/9 windows, size under Settings → Multiview) where every window is an independent stream: mix channels from different playlists/accounts, click a window for audio focus (red border marks the audible one), right-click for mute, **audio/subtitle track choice**, **move/swap between windows** or remove. Catch-up channels get a **real timeshift timeline** — programme ticks from the EPG, hover names the programme, drag to any point in the provider archive, a LIVE pill jumps back to the edge. Each window shows its channel, current programme and source playlist; overlays, controls and the mouse cursor auto-hide; double-click maximizes; viewing is recorded in History. Send the currently playing video there via right-click, and starting playback in the main window offers to close multiview (configurable). A Settings → Multiview tab collects everything: grid size, title bar/always-on-top, remembered window geometry, auto-hide delay, seek step, audio behavior and the connection-conflict policy.
- **Identical icons on every OS** — the sidebar and control icons are now a hand-drawn vector set (no more emoji fonts that rendered differently on Linux/macOS/Windows), the playlist switcher is a compact icon chip that reveals the active playlist's name on hover, the logo (= jump to now playing) stays on the collapsed rail, and EPG Guide is reachable from right-click menus (TV entry, live categories, channels).
- **Settings overhaul** — a new Multiview tab; number fields with real stepper arrows and dropdowns with visible ▼ on every OS (drawn assets); checkboxes that are actually visible on the OLED pure-black theme; a custom-painted tab bar with identical packed spacing on every platform; the dialog sizes itself to fit.
- **Favorites everywhere** — add/remove favorites from History, Watch Later and Watched; the grouped "All favorites" view routes movies/series to the right store (remove used to silently miss) and no longer auto-plays a movie row as a live channel.
- **Fixes** — resume prompt now appears for movies/series played from Favorites; History entries mis-filed as TV channels are healed automatically and play again; a pending auto-preview can no longer stomp a just-started playback; switching playlists no longer forces a full EPG re-download; macOS: no duplicate app menu, no clipped sidebar icons, and Settings → Reset all settings no longer crashes on exit.
- **Remembered tracks on resume** — the audio/subtitle track you pick for a movie, episode or recording is remembered per title and re-applied when you replay or resume it (picking a subtitle track now also always makes it visible).
- **Performance** — dragging the column dividers is much smoother (icons are cached instead of re-drawn, redundant list relayouts are skipped); app startup is faster (Chromecast support now loads on first use instead of at launch); multiview uses far less memory when the grid mixes plain live channels with catch-up ones (the deep rewind buffer is only allocated where it can be used).

## [0.7.3]

**A real pop-out player, simpler playback, and stream errors that finally
tell you what's wrong.**

- **Pop-out player** — detach the video into its own window (the ⧉ button in the player bar, or `P`) and keep it on a second screen while you browse and zap in the main window. It's the *same* in-app player moved out, so the full control bar, seeking and channel-zapping all come with it. By default it's a clean, title-bar-less video window you move by dragging the video; right-click it for **Always on top**, **Show title bar** or **Auto-hide controls**, double-click for fullscreen, `Esc` to leave it. This replaces Picture-in-Picture, which it fully supersedes.
- **Simpler playback** — removed the confusing "Playback mode" setting. The embedded player is the player; "Open externally" (mpv/VLC) stays on the right-click menu. The two old modes ("reused mpv window" and "external") were buggy or redundant and are gone.
- **Stream errors in plain language** — when a channel won't play, the app now says *why* instead of "loading failed": expired subscription, all connections in use, provider blocked the stream (incl. the non-standard `458`), server unreachable/timeout, or a format it can't play. No debug mode needed.
- **Troubleshooting** — `DOPEIPTV_LOG=debug` now logs every provider call (auth state, connections, HTTP status) with credentials redacted, and `DOPEIPTV_LOG_FILE=/path` tees it to a file for bug reports.

## [0.7.2]

**Windows joins Linux and macOS** — a portable Windows x64 build (unzip and
run, no installer) — plus timeshift and UI fixes.

- **Windows (portable)** — unzip the folder and run `dopeiptv.exe`, no installer and no admin rights; an optional Start-menu/desktop shortcut under Settings → Interface → Maintenance, a bundled README, and a fix for a crash on exit in the windowed build.
- **Timeshift** — channels that advertise catch-up but don't actually serve it now hide the timeshift affordance (list marker, in-player rewind button and seek-bar overlay) instead of leaving it behind an "archive unavailable" message. Channels with a genuinely shorter archive are unaffected.
- The category-search magnifier now sits centred in its button.
- Website: grouped, plain-language download page (OS + CPU), a subtle per-file download counter, and macOS/Windows first-launch notes.

## [0.7.1]

A **UI/UX polish** release: a redesigned, responsive left panel and a calmer
timeshift player.

- **Redesigned left panel** — icons for every nav item, a Browse/Library split with a collapsible Library group, and Guide/Settings as a compact side-by-side pair. The sidebar auto-collapses to the icon rail when the window gets narrow.
- **Responsive layout** — the middle-column controls go compact on a narrow column, the player's control row never drops the volume, and everything below the video lives in one scroll column so the logo/programme info can't overlap the picture. A minimum window size prevents squeezing panes into overlap.
- **Timeshift timeline auto-hides** after a few idle seconds and reappears on interaction.
- Accent colours follow your chosen theme everywhere (EPG "now" progress bar, playing highlights); the sidebar collapses correctly in the Watched/Trakt lists; About → website link and better release-notes formatting.

## [0.7.0]

**Timeshift &amp; catch-up TV**: scrub back into a channel's archive on a live
timeline, pause live TV, or jump to a specific past programme — plus a
**reminders manager**, **search everywhere**, **customizable keyboard
shortcuts**, and **rock-solid video playback** by default.

<details>
<summary><b>Full notes</b></summary>

### Timeshift, catch-up &amp; DVR
- **Live timeline** on timeshift channels — scrub back into the provider's archive and watch what already aired.
- **DVR-style pause** — pause live TV and resume behind live; the player shows how far behind live you are and drops the LIVE tag the moment you pause.
- **Pick a specific past programme** from the guide and play just that show, with a seek bar clamped to the programme's own length.
- **Archive-depth learning** — the app learns how far back each channel really reaches, adapts the “go back” span to it, and recovers gracefully from over-deep requests instead of dropping the channel.
- **Catch-up from History** — resume timeshift straight from a channel in your History, with a catch-up marker on those rows.
- **Per-channel reset** — right-click a timeshift channel to clear its learned archive state.
- Amber timeshift markers, a **Go-live** button, and arrow-key scrubbing (fine-seek inside a segment, Shift+arrow for a coarse timeline step).

### Reminders &amp; guide
- **Reminders manager** — find, review and multi-delete programme reminders in one place; the store is crash-proof against decorated rows.
- **Programme search across the whole guide** — find a show by name anywhere in the EPG.
- **Configurable “upcoming programmes”** in the detail pane (default 5, adjustable).
- Guide polish: a **Close** button, centring over the main window, and deeper EPG fetches.

### Search everywhere
- **Category search** — match category names *and* the channels inside them, with smart ranking; collapsible on the sidebar.
- **Left-column search** in Favorites, Watch Later, Watched, Recordings and History.
- **Jump-to-now-playing** (sidebar logo) also selects the playing channel's category.

### Keyboard &amp; navigation
- **Customizable keyboard-shortcuts editor** with sensible per-OS defaults.

### Video &amp; playback
- **Software decoding is now the default** — like standalone mpv. Modern CPUs handle even 4K 10-bit HEVC/HDR comfortably, and it's immune to the GPU/driver render hazards that could black out hardware-decoded video with subtitles (e.g. the nvidia-open stack).
- **Hardware decoding is an opt-in** setting (Settings → Playback → Video) for those who want it, with mpv's `hwdec-software-fallback` as a safety net for genuine decoder failures.
- **Video filters** — optional deinterlace, sharpen and HDR tone-mapping, plus a **Video** menu in the in-player options.
- Faster channel switching via a light stream probe for live zapping (full analysis is kept for VOD).

### Recordings
- **Editable recording title &amp; description.**
- Recording **stops cleanly when you switch the channel** it's capturing.

### Settings &amp; interface
- Playback settings are **grouped and scrollable**, and **scrolling the page never changes a control** — you have to click into it.
- **Themed spin boxes** (fixes the white-on-white “upcoming count” box) and a tidy **Maintenance** row.

### Fixes
- **Fixed an exit segfault** on newer Python during teardown.
- **macOS:** arrow-key scrubbing works, and Picture-in-Picture stays floating.
- Audio no longer goes silent after switching a movie to a TV channel.
</details>

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
