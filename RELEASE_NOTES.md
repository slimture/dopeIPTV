## dopeIPTV 0.8.0

**Multiview — watch up to nine live channels at once** — plus a sharper,
truly cross-platform interface and a long list of fixes.

**Highlights:** **multiview grid (2/4/6/9 windows, even across playlists)**
· **per-window audio/subtitles and a real timeshift timeline** · **identical
hand-drawn icons on Linux/macOS/Windows** · **a Settings → Multiview tab**
· **favorites from History/Watch Later/Watched** · **many macOS fixes**.

<details>
<summary><b>Full release notes</b> — click to expand</summary>

### Multiview — new
- **Watch several live channels at once** in a separate grid window: **2 (1×2), 4 (2×2), 6 (2×3) or 9 (3×3)** windows, chosen under Settings → Multiview. Open it from the sidebar's ▦ button, or right-click a channel → **Add to multiview** and pick the target window (each entry shows what it would replace).
- **Every window is its own stream** — mix channels from **different playlists/accounts** to sidestep a single account's connection limit. A one-time notice explains the connection cost; the sidebar's playlist chip switches accounts in one click.
- **Click a window for audio focus** — the red border marks the audible one, the rest stay muted. Right-click for **mute**, **audio track**, **subtitles** (only shown when the stream actually has them), **move/swap between windows**, or **remove**.
- **Real timeshift on catch-up channels**: the seek bar is an archive timeline with **programme ticks from the EPG** — hover names the programme, drag to any point in the provider's archive, arrow keys step (step size configurable), and a **LIVE pill** jumps back to the live edge. Plain channels get a session scrubber instead.
- Each window shows its **channel, current programme and source playlist**; titles, controls, the close button and the **mouse cursor auto-hide** and return on movement. **Double-click maximizes** the grid; Esc steps back out. **Space** pauses the focused window.
- **Send the playing video to multiview** from the docked player's right-click menu — the connection carries over instead of doubling up (configurable). Starting playback in the main window while multiview runs asks whether to close it — or set an always-close/always-keep policy.
- Multiview viewing is **recorded in History**, and the window's size/position is remembered.

### A truly cross-platform interface
- **Every icon is now hand-drawn vector art** — sidebar navigation, Guide/Settings/Multiview, search, sort, grid and player controls render **pixel-identically on Linux, macOS and Windows** (they used to be emoji glyphs, which every OS drew differently — sometimes clipped or invisible).
- The **playlist switcher is a compact icon chip** under the logo: hover reveals the active playlist's name, it hides entirely with a single playlist, and it updates the moment you add/remove one. The logo (= *jump to now playing*) now stays on the collapsed icon rail, scaled to fit.
- **EPG Guide via right-click** — on the TV nav entry, on live categories and on channels in the list.

### Settings overhaul
- A new **Multiview tab**, grouped into Window / Behavior / Controls.
- **Number fields have real stepper arrows** and **dropdowns a visible ▼** on every OS (drawn image assets — the old style-drawn arrows simply didn't render on macOS).
- **Checkboxes are clearly visible on the OLED pure-black theme** (bordered indicator, accent fill, drawn check mark).
- The **tab row is custom-painted** — identical packed spacing and selection chip on every platform — and the dialog sizes itself to fit it.

### Favorites everywhere
- **Add/remove favorites straight from History, Watch Later and Watched** — movie, series and channel rows each get their proper menu (History channel rows even get multiview/timeshift/record).
- The grouped **"All favorites"** view now routes movies/series to the right store — *Remove from favorites* used to silently miss them — and no longer auto-plays a movie row as a live channel.

### Remembered tracks & performance
- **The audio/subtitle track you pick is remembered per title** — replay or resume a movie, episode or recording and it comes back with the same subtitles/audio instead of the stream default. Picking a subtitle track now also always makes it visible.
- **Smoother divider drags** — the drawn icons are cached instead of re-rendered on every pass, and redundant list relayouts are skipped.
- **Faster startup** — Chromecast support loads on first use of the Cast menu instead of at every launch.
- **Leaner multiview** — the deep rewind buffer is only allocated for catch-up-capable channels; plain live windows use a fraction of the memory ceiling.

### Fixes
- **Resume works from Favorites**: movies/series played from the Favorites sections now prompt to continue and save their position (they used to be treated as live channels).
- **History healed**: entries mis-filed as TV channels by the old bug are re-typed automatically (the stored URL knows the truth) and play again; a pending auto-preview can no longer stomp a playback you just started.
- **Switching playlists** no longer forces a full EPG re-download — the guide loads from cache when fresh; manual Refresh and the auto-refresh schedule still fetch anew.
- **macOS**: no duplicate app menu, no clipped sidebar icons, and Settings → **Reset all settings no longer crashes on exit** (and a reset no longer re-seeds the cleared config on the way out).

> Linux is and remains the primary target — Windows and macOS are a bonus.

</details>
