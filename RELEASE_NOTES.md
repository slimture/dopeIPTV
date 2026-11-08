## dopeIPTV 1.4.0

Rewind with dopeIPTV's own buffer, and a programme guide that keeps itself
up to date.

- **Pause as long as you like (experimental).** Turn on **Settings →
  Playback → Rewind with dopeIPTV's own buffer**: dopeIPTV keeps the channel
  itself, on disk, and goes on downloading while you pause - so **LIVE**
  takes you back to live without reconnecting, and you can go back as far
  as you set, within the space you give it.
  Each channel you leave keeps its history for when you come back, and
  **Record** records from the buffer - whole even while you pause, and
  from where you are when you have gone back. Without it, a recording
  stands still while you pause, and the app now tells you so.
- **A new Home.** A featured strip at the top with this week's most
  popular titles your provider has - big artwork, Play and More info -
  continue watching with the time left, your favourite channels with what
  is on now and how far it has got, the popular rows with their chart
  position, and arrows instead of scrollbars.
- **The guide no longer stays on yesterday.** Left open overnight - or on
  a Mac that slept - the app kept yesterday's guide until it was started
  again. It now looks at the guide every 20 minutes and when you come back
  to the app, and fetches it again when it is old or about to run out:
  when your provider updates its guide, the new programmes show up within
  the hour.
- **A clearer note where the provider's guide ends.** When a channel's
  guide stops before now, the detail panel says so, instead of suggesting
  that the times were wrong.
- **Refresh the guide from the guide.** A ↻ button in the programme guide,
  and "Refresh guide now" when you right-click it.
- **One bar for rewinding.** On a channel with your provider's catch-up,
  the DVR row and the timeshift timeline no longer lie one above the
  other: **DVR | TIMESHIFT** at the left of the bar switches between them,
  and the app remembers which you like.
- **Stalker / Ministra portals (experimental).** Add a provider that gave
  you a portal address and a MAC address: **Settings → Playlists → Add →
  Stalker / Ministra portal**. Channels, films, series and the guide's now
  and next; catch-up from a portal is not there yet.
- **Intel Macs are back.** A `.dmg` for Intel (x86_64) Macs again,
  beside the Apple Silicon one.
- **Help test what is coming.** **Settings → General → Update channel →
  Test builds** offers the test builds of the next version as soon as
  they are built. They can have faults a release does not; back on
  **Releases**, you keep the test build until the next release.

Fullscreen video flickering around its edges on Linux? That is variable
refresh rate (VRR): turn it off in your display settings and it stops.

On the AppImage from 1.2.15 or later? Click **Update now** in the banner.
Coming from an older version, what changed since is below - from 1.2.14
or earlier, everything new in 1.2.15 as well.

## dopeIPTV 1.3.0

Pause and rewind live TV, a new look, subtitles your way, a taller video
and country flags in the category list on every system - and an app that
stays quick with a big provider while its guide and lists load.

- **Rewind live TV.** Move the pointer over the video and a bar takes you
  back in the channel you are watching; **LIVE** brings you back, and the
  arrow keys and pause work too. **Settings → Playback → Rewind live channels** sets how far
  back - 2 minutes by default. Turn on **Keep the rewind buffer on disk**
  to go back up to 6 hours without using memory. Paused for long, the
  button reads **↻ LIVE**: it opens the channel again at live, and the
  rewind starts over.
- **A new look.** Eight new themes - Tokyo Night, Rosé Pine, One Dark,
  Everforest, Kanagawa, High contrast and two light ones - picked from
  small pictures of the app under **Settings → Appearance**. The accent is
  a row of colours: three new ones, the theme's own, or any colour you
  like. The app can follow your desktop's light and dark mode, corners can
  be sharp or extra rounded, and text and controls can be made larger or
  smaller.
- **Subtitles your way:** size, colour and a dark box behind them - under
  **Settings → Audio & subtitles**, or right-click the video → **Subtitles**.
- **Make the video taller.** Drag the small handle under the video; the
  height is remembered. On Windows the detached player now resizes from
  its edges and corners.
- **Country flags in the category list**, on Windows too, for categories
  named with a flag or a country code ("SE | Sport", "DE: News").
- **Quick with a big provider.** Opening Movies while the guide loaded
  could take 20 seconds and is now instant. Search, "All" with tens of
  thousands of films and the list/grid switch react at once, and the
  window and the playing video no longer stall while the guide, the lists
  or the poster cache load or save in the background.
- **About dopeIPTV** on Wayland no longer lays the release notes over its
  buttons.

The live rewind, the video height and the flags are a contribution by Luca
Merega (lmerega on GitHub) - thank you!

## dopeIPTV 1.2.18

What is popular right now, on Home - and fixes for the AppImage and .deb
on a current Linux distro and for subtitles with the full stop in front.

- **Popular right now, on Home.** Two new shelves, **Popular movies right
  now** and **Popular series right now**: this week's most popular films
  and series on TMDB that your provider has. Either can be switched off
  under **Settings → Home**.
- **The AppImage and .deb on a current Linux distro** (Arch, and others
  as they update) printed a page of font and Mesa errors at every start.
  They now use the system's own font and graphics libraries.
- **Subtitles with the full stop in front (".Hej då").** A subtitle
  translated from Arabic or Hebrew could put the full stop at the start
  of a Swedish or English line. It is at the end again.

## dopeIPTV 1.2.17

The series you follow get a place in Settings, and OpenSubtitles' daily
limit is explained.

- **Followed series in Settings.** **Settings → Home → Followed series**
  lists the series you follow, and **Stop following series** stops one
  without having to find it first. A switch there turns the notifications
  about new episodes off - new episodes still show on Home under **New
  episodes**, and with a dot in the episode list.
- **OpenSubtitles' daily limit, explained.** Without an account
  OpenSubtitles allows 5 subtitles a day per internet connection, with a
  free account 20. **Settings → Audio & subtitles** says so, and when the
  day's downloads are used up the search tells you so in your language,
  with where to enter a free account.
- **Settings → Local files** lists your folders properly, each with its
  full path when you point at it.

## dopeIPTV 1.2.16

A fix for subtitles from OpenSubtitles on macOS, Windows and the Flatpak.

- **Subtitles from OpenSubtitles load again.** The subtitle you picked was
  downloaded and loaded, but the app said "OpenSubtitles did not send a
  usable subtitle file" and kept the search open. The setting "On - forced
  subtitles only" works there again as well, and a subtitle you picked for
  a title is always shown when you play it again.

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
