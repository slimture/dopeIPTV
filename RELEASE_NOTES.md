## dopeIPTV 0.7.1

The **Windows + polish** release: dopeIPTV now runs on **Windows** (portable
build — unzip and run, no install), alongside a big pass of **responsive UI**
and **timeshift** refinements. Linux and macOS are unchanged.

**Highlights:** **Windows support** · **redesigned left panel** (icons + a
collapsible library) · **responsive layout** that never overflows or overlaps ·
**timeshift timeline auto-hides** · **accent colours follow your theme
everywhere**.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Windows
- **New Windows build** — a portable ZIP: unzip the folder and run `dopeiptv.exe`, nothing to install (works on locked-down machines). libmpv/FFmpeg bundled. Because it isn't code-signed yet, SmartScreen may prompt once — **More info → Run anyway**.
- Fully separate from the Linux/macOS builds; those code paths are untouched.

### Left panel
- **Icons for every nav item**, painted in the theme's muted tone (white when selected) and re-tinted when you change theme or accent — labels stay perfectly aligned.
- **Browse / Library grouping** — TV/Movies/Series on top, your personal lists (Favorites, Watch Later, Watched, Recordings, History) under a **collapsible Library** header (same disclosure arrow as Categories), remembered across sessions.
- Guide and Settings are now a compact **side-by-side** pair instead of two stretched buttons, and the sidebar **scrolls** on short screens so they're always reachable.

### Responsive layout
- The sidebar **auto-collapses to the icon rail** when the window gets too narrow, and expands again when there's room — without fighting a manual choice.
- The middle-pane control strip **goes compact** on a narrow column (captions hide, combos shrink, the grid toggle becomes a glyph) while the dropdowns still open full-width.
- The docked player's **control row never crushes or drops the volume**, and everything below the video lives in **one scroll column** so the channel logo and programme info can never overlap the picture — at any window size.

### Timeshift
- The **live timeline auto-hides** after a few idle seconds (like the seek bar) and reappears on mouse-move or a keyboard step, instead of sitting over the picture forever. Shift+arrow archive-stepping keeps working even while it's tucked away.

### Fixes & smaller touches
- The sidebar now **collapses correctly in the Watched / Trakt lists** (the red "Sync now" button no longer pins it open).
- Middle-column accent colours (the EPG "now" progress bar, playing highlights) **follow your chosen accent/theme** instead of a fixed blue.
- An **About → website** link (iptv.dope.rs), and a minimum window size so panes can't be squeezed into overlapping.

</details>
