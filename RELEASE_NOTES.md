## dopeIPTV 1.2.11

Mostly Linux this time: the desktop icon, the links in the About window,
and a handful of things that went wrong when a list and the player
disagreed about what you had selected.

### Linux desktop

- **The app appears in your menu, with its icon.** On Wayland GNOME and
  KDE take a window's icon and name from the .desktop entry matching it
  and ignore what the app itself sets — so run from an AppImage, where
  nothing has ever installed one, the taskbar showed a blank placeholder
  however good the icon inside the download was. The app now offers, once,
  to add an entry; say no and nothing is written, and the same switch
  lives under Settings > Playback. Installed from the `.deb`, the entry is
  already there and nothing is asked.
- **The icon is the same size as everyone else's.** It was drawn edge to
  edge while every icon beside it carries a margin — GNOME asks for about
  80% of the canvas, which is the same thing Apple asks for and the same
  fix the macOS Dock icon got in 1.2.10.
- **An icon that will not appear at all** now has a cause you can read.
  Where a stale `icon-theme.cache` exists, GTK reads that instead of the
  directory and an icon added since is invisible — the app drops one older
  than its own icons, and writes a line to the log saying what it found.
- **Links in the About window work.** Website, download, the Trakt pages:
  all of them did nothing from a packaged build. Opening a link starts a
  browser as a child of this process, and it was inheriting the bundle's
  library paths and falling over before it drew anything — exactly the
  fault external mpv had, and the same cure.

### Watching a series

- **Turning grid view on and off inside a series keeps the episodes.**
  It dropped back to the series list instead, and double-clicking there
  then tried to play the series itself: an error, and whatever was playing
  stopped. Three related faults behind it, all the same shape — a list row
  and the player disagreeing about what had been selected — and Favorites,
  Watchlist and Watched had their own versions of it.
- **A hidden channel no longer hides a film.** Provider ids are only
  unique within a type, so hiding live channel 123 could hide movie 123 as
  well.

### Elsewhere

- **The right-hand panel has one scrollbar, not two.** The info box was
  its own scroll area inside the panel's, and the wheel — a trackpad
  especially — went to whichever of them the pointer happened to be over.
- **The "Next episode" card sits where the picture ends**, rather than a
  control bar's height above it when there is no control bar there.
- **Your account stays out of the log.** An Xtream stream URL carries the
  whole account in it, and these logs are meant to be shared in bug
  reports. Credentials are now masked wherever they appear — the URL
  shapes we know, and the account's own values everywhere else.
- **The Chromecast bridge only answers for its own files.** It listens on
  the LAN so the television can reach it, so everything else on the
  network can ask it too; it now names what it will serve instead of
  guessing at what to refuse.
- **macOS can try the other fullscreen path.** Start with
  `DOPEIPTV_MAC_RASTER=1` to use the presentation path that cured this on
  Linux and Windows. Opt-in, so both can be compared from one build.

### macOS: Apple Silicon only

**1.2.10 was the last release with an Intel `.dmg`.** Homebrew moved macOS
x86_64 to Tier 3 in September 2026 and stopped building the packages the
Intel build was assembled from; Apple has said macOS Tahoe 26 is the last
macOS for Intel. Apple Silicon is unaffected.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target - Windows and macOS are a bonus.
