## dopeIPTV 0.6.1

A polish release: a collapsible sidebar, custom colours across the app, a
smarter M3U/EPG setup, and some important fixes (Picture-in-Picture no longer
quits the app, the colour picker no longer freezes).

**Highlights:** collapsible **icon-rail sidebar** · **focus mode** · custom **colours** for nav, categories, items & favourite folders · **M3U auto-EPG** · EPG grid flags the **playing** channel · PiP-close & colour-picker fixes.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Sidebar & layout
- **Collapsible sidebar** — fold it down to a slim icon rail with the **☰** button or **Ctrl+B**, or just drag its divider: inward collapses to the rail, outward expands it again. You keep the TV/Movies/Series navigation as icons and reclaim the width for the content — handy on small screens.
- **Solo category** — a small disclosure arrow next to "Categories" collapses the list to only the active category (tidy for screenshots), without touching the navigation.
- **Focus mode** — the **⤢** button (or **Ctrl+Shift+M**) hides the whole content list for a rail-plus-player layout. A slim arrow strip on the player's edge brings the list straight back, so it can never get lost.
- The content list now has a minimum width, so dragging a divider can't accidentally hide it.

### Colours
- **Left-column entries** (TV … History) can each be given their own **text and background colour** — right-click an entry.
- **Categories** can be coloured too; a category's colour tints **its own row** in the left column.
- **Individual channels, movies and series** can be given a personal text/background colour from their right-click menu.
- **Favourite folders** can be coloured, and that colour applies to **every favourite in the folder** — a quick way to colour-code a whole group.
- Every colour choice has a **Reset**, and colours persist between sessions.

### M3U & EPG
- **Automatic EPG for M3U playlists** — dopeIPTV now reads the `url-tvg` / `x-tvg-url` guide URL from a playlist's `#EXTM3U` header, so a plain M3U URL brings its programme guide with nothing extra to configure (an explicit per-playlist EPG URL still wins).
- **Gzipped XMLTV** guides (`.xml.gz`) are now decompressed transparently.
- The EPG timeline grid **flags the channel you're currently watching**, and a **"Playing"** button jumps the board back to it after you've scrolled down a long line-up.

### Convenience
- **Refresh playlist** is now one right-click away on the **TV / Movies / Series** entries in the left column.

### Fixes
- **Picture-in-Picture** — pressing the window's close button while in PiP now drops back to the normal mini player instead of quitting the whole app.
- **Colour picker** — no longer freezes the app on some Linux setups (it now always uses the in-app dialog).
- **Keep your place** — opening *Manage categories*, or recolouring a category, no longer snaps the category list back to the top; your selection and scroll position stay put.

</details>

**Full changelog:** https://github.com/slimture/dopeIPTV/compare/v0.6.0...v0.6.1
