## dopeIPTV 1.2.0

A dedicated place to manage upcoming recordings, a smoother maximize on macOS,
and a batch of pop-out and Home polish.

- **Manage your scheduled recordings in one place**: a new **Scheduled
  recordings** panel lists every pending recording (recording now + scheduled),
  soonest first, with *Edit start/stop time* and *Cancel* right there. Open it
  from the player's REC menu or a new button in the EPG guide — no more hopping
  between the sidebar and the middle column. Upcoming recordings also now show
  at the top of the default Recordings view, and the list refreshes live.
- **Smoother maximize on macOS**: maximizing the mini player now uses the same
  frameless mirror window as the pop-out, so it snaps to fullscreen instantly
  instead of the slow native fullscreen animation — with the full control bar,
  auto-hiding controls and a hidden cursor while maximized. Every way out docks
  straight back.
- **Pop-out polish**: the control bar auto-hides after a few idle seconds
  (returns on mouse movement), the mouse cursor hides over the video, and a
  crash when opening the pop-out (a stale seek-bar tooltip) is fixed.
- **Sleep-timer countdown**: a small pill shows how long is left before playback
  stops. It auto-hides with the other controls but pins on — and turns red — for
  the final 30 seconds.
- **"Watch the recorded channel" now plays right away** even the instant a
  recording starts (it waits for the file instead of silently giving up).
- **Clearer update notices**: a dismissible "new version available" banner now
  shows across the top of the window (visible on Home too, not just a sidebar
  badge), and Download opens the website. The About dialog's release notes are
  readable again (they were an unthemed white box).
- **Starts on TV by default now**: Home is an opt-in — turn on "Open Home on
  startup" in Settings to land there instead.
- **Home**: resuming a movie lands in the movie's own category with it selected,
  and TV-channel logos are centred in their tiles.
- **Reset all settings really resets everything now**: Watched, Watch Later and
  resume positions lived in separate files and used to survive a reset — all
  three are cleared too.

> **Windows pop-out is experimental** in this release: it now uses the macOS
> mirror approach but has **not been sufficiently tested on Windows** — treat
> Windows pop-out as unstable for now.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target — Windows and macOS are a bonus.
