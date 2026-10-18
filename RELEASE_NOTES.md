## dopeIPTV 1.2.15

A new Settings window, the whole app in your own language, Trakt working
again - and the AppImage now updates itself.

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
  client_id is required" after Trakt moved its sign-in. If you were
  connected before, you may be asked to connect once more.
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
