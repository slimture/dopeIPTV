## dopeIPTV 1.0.0

The 1.0 milestone — dopeIPTV now speaks your language, sets up from a single
pasted link, and scrolls large lineups without a stutter.

- **27 languages**: the whole interface is translated into English, Svenska,
  Español, Deutsch, Français, 中文, Русский, ไทย, Português, Italiano,
  Nederlands, Polski, Hrvatski, Srpski, Ελληνικά, Türkçe, Українська, Bahasa
  Indonesia, Tiếng Việt, हिन्दी, 日本語, 한국어, Kiswahili, العربية, فارسی,
  עברית and اردو — switchable live in Settings, with full right-to-left
  layout for Arabic, Persian, Hebrew and Urdu.
- **One-paste setup**: paste your whole Xtream link (or an M3U URL) into the
  welcome screen and dopeIPTV recognises the type and fills in the
  server / username / password for you. Typing the three fields by hand still
  works exactly as before.
- **Snappier lists**: channel/movie/series logos and posters are scaled once
  and cached instead of on every repaint, removing the main source of scroll
  lag with large lineups — with smooth pixel-granular scrolling. The video
  pipeline is untouched.
- **For contributors**: native-speaker translation fixes are welcome (see
  `docs/TRANSLATING.md` and the new "Translation fix" issue template); the
  pure-logic core is now strictly type-checked, and every module and class is
  documented.

Everything from the 0.9.0 release — Home, the interactive EPG guide,
reminders & recording, timeshift, recording, multiview, themes and instant
outage-proof startup — is here too.

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
