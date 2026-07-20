## dopeIPTV 1.1.0

Video stability on macOS, smarter series navigation, a leaner Home and a
faster app all around.

- **Pop-out video fixed for real**: docking the player in or out of the
  pop-out window could leave a frozen frame (or black) while audio kept
  playing — the video surface now rebuilds its framebuffer on every
  dock/undock, with the stream untouched. Entering video fullscreen is one
  clean cut, with no half-size flash.
- **Series navigation that lands right**: *Now playing* jumps to the playing
  episode inside its series; a Continue-watching card on Home plays the
  episode *and* opens the series' episode list; backing out of an episode
  list lands in the series' own category with the series selected.
- **Recently viewed treats episodes as episodes**: replays resume where you
  left off (no more restarting from zero), rows read "Series · S1 E2 -
  Title", duplicates are gone, and cards reliably show the series' poster.
- **Home, decluttered**: the redundant Featured row is removed, a new
  **Watch Later shelf** shows your saved movies and shows, and Continue
  watching / Recently viewed no longer overlap.
- **The TV guide from Home or Movies covers your favourite channels** (the
  lineup you curated) instead of an arbitrary first category — the full
  lineup if you have no favourites. TV and Favorites scoping is unchanged.
- **Faster**: quicker startup (lazy imports), Settings opens ~30% faster,
  fullscreen toggles paint in one clean cut, image caches are bounded
  (2.5 GB disk budget with automatic pruning), and an optional custom
  allocator can be enabled with `DOPEIPTV_JEMALLOC` (off by default).
- **Settings**: the language picker scrolls properly on every platform
  (with a restart hint), a pasted provider link now survives messy input
  (pre-filled fields, text prefixes), and the old one-time 0.9.0 settings
  migration is fully removed.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target - Windows and macOS are a bonus.
