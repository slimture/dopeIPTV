## dopeIPTV 1.2.16

A fix for subtitles from OpenSubtitles on macOS, Windows and the Flatpak.

- **Subtitles from OpenSubtitles load again.** The subtitle you picked was
  downloaded and loaded, but the app said "OpenSubtitles did not send a
  usable subtitle file" and kept the search open. The setting "On - forced
  subtitles only" works there again as well, and a subtitle you picked for
  a title is always shown when you play it again.

On the AppImage from 1.2.15? Click **Update now** in the banner. Coming
from 1.2.14 or earlier, everything new in 1.2.15 is below.

## dopeIPTV 1.2.15

Cast to your smart TV, subtitles from OpenSubtitles, your desktop's media
controls, and word when a series you follow gets a new episode - plus a
new Settings window, the whole app in your own language, Trakt working
again, and an AppImage that updates itself.

### Four new things

- **Cast to your smart TV.** Most smart TVs (Samsung, LG, Sony, Philips,
  Panasonic) now show up in **Cast to TV…** next to your Chromecasts,
  marked "(DLNA)". Pause, seek and volume work from the cast strip, and
  local files can be cast too. Nothing extra to install.
- **Subtitles from OpenSubtitles.** In the player's **Subtitles** menu,
  **Find subtitles online…** searches for the film or episode you are
  watching and loads the one you pick - and it comes back with that title
  next time. If it asks for an API key, get a free one at
  opensubtitles.com and enter it under **Settings → Audio & subtitles**.
  You can also **load a subtitle file** from disk.
- **Your desktop's media controls (Linux).** The media keys on your
  keyboard, headphone buttons, GNOME's and KDE's media controls and KDE
  Connect on your phone now pause, play and skip - and show what is
  playing, with its logo.
- **Follow a series.** Right-click a series → **Follow series**. When new
  episodes arrive they appear on Home under **New episodes**, with a
  notification, and opening the series takes you to the first new one.

### A new Settings window

- **Easier to find things.** A sidebar with fifteen pages - General,
  Appearance, Playback, Audio & subtitles, Video, Guide, Playlists and
  more - replaces the long row of tabs, and the **search field** at the
  top finds any setting by name.
- **Clearer pages.** Settings are grouped in titled cards, with a short
  explanation under the ones that need it, and every yes/no choice is a
  switch.
- **Playlists at a glance.** Each playlist shows its server and an
  "In use" badge. **Drag playlists** into the order you want - the
  playlist menu follows it.
- Your existing settings are kept exactly as they were.

### In your language

- **About 200 texts that were still in English are now translated** into
  all 27 languages: recording messages and why a recording stopped,
  timeshift, list counts, the detail panel, player badges, and error
  messages from your provider, Trakt, Chromecast and the updater.
- **Dates and weekdays** in the guide and recordings follow the app's
  language, and so do the standard buttons in dialogs (OK, Cancel, Yes,
  No).

### New

- **The AppImage updates itself.** "Update now" in the new-version banner
  downloads the new AppImage, checks it, test-starts it and only then
  swaps it in - your dock icon keeps working.
- **Drop a playlist on the window.** Drag an `.m3u` file or a provider
  link onto dopeIPTV to add it as a playlist.

### Fixes

- **A rare crash under heavy load is fixed.** Cleaning up memory on a
  background thread could pull a timer out from under the interface; that
  cleanup now always happens on the interface's own thread.
- **Trakt sign-in works again.** Connecting ended with "invalid_client:
  client_id is required": Trakt had retired the app dopeIPTV signed in
  through. dopeIPTV has a new one, set up for Trakt's 2026 sign-in, so if
  you were connected before, connect once more under Settings → Trakt.
- **M3U playlists with å, ä, ö** and other letters no longer show "Ã¥"
  when a server names the wrong character set.
- **Multiview:** Space, M and F act on the window you are watching, and
  the screen no longer sleeps while multiview plays.
- **Favorites:** renaming a folder onto another no longer duplicates
  channels, and a folder's colour follows it.
- **The guide** recovers by itself from a damaged cache instead of failing
  on every start.
- A pop-out player saved on a monitor that is no longer connected opens on
  this one, and Windows no longer crashes in the detail pane's schedule.
- **macOS, first start:** right-click → Open no longer works since macOS
  15. Open the app once, then allow it under **System Settings → Privacy &
  Security → Open Anyway** and confirm with your password.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target - Windows and macOS are a bonus.
