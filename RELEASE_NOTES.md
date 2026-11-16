## dopeIPTV 1.6.0

**A profile for everyone at home, a TV mode for the sofa with your phone as its remote, a page for every series, and Jellyfin, Emby and Plex as sources.**

### The big ones

- **Profiles.** One per person in the household, each with their own favorites, history, Continue watching, watched marks, Trakt or Simkl, and theme. "Who's watching?" when the app starts, a PIN if you like, and kids profiles that never see a locked or adult category. Switch from your avatar - no restart. **Settings → Profiles.**
- **A page for every series.** Open a series and it gets a page of its own, as on Plex or Jellyfin: the artwork, one button that plays on from where you stopped, the seasons in a row and the episodes as cards with their picture, plot and progress. Back takes you to the list exactly where you were.
- **TV mode.** A full-screen layout made for the sofa, run from a remote, a game controller or LIRC: Home, channels with what is on, the guide, movies and series in posters, and search with a keyboard on screen. **TV mode** in the menu, or `Ctrl`+`Shift`+`T`.
- **Your phone as a remote.** Scan a code, and your phone's browser shows your channels and favorites, what is on now and next, the volume and channel up and down - and in TV mode it becomes a remote with a d-pad or a touchpad. Add it to the home screen and it opens as *dopeIPTV Remote*. Nothing to install, and only your home network reaches it. **Phone remote…** in the menu.
- **Jellyfin, Emby and Plex.** Your own films and series as a source, with the server's artwork, where you stopped and what you have watched - and what you watch here goes back to the server. Live TV too, where the server has it.
- **More boxes at home.** SAT>IP servers - a Fritz!Box Cable, Octopus NET, Digibit, minisatip and the like - and VDR, DVBViewer Media Server, NextPVR and MythTV join Enigma2, Tvheadend and HDHomeRun, with their channels, guide and recordings.
- **Your Enigma2 box from the app.** Its signal, a screenshot of its TV, its remote control and its recordings; **its channel lists edited from the computer**, with a backup first and Undo; its AutoTimer rules beside the app's own series rules; and a question first when a stream would cut off a recording or what the box's TV shows.
- **dopeIPTV as the home's server** (Nerd, off by default - or `dopeiptv --server` on a NAS or a Raspberry Pi). VLC, Kodi and a TV's IPTV app watch your channels through it, and Plex, Jellyfin and Emby find it by themselves as an HDHomeRun tuner. Each channel is fetched once for everyone watching it, and never more at once than your account allows - your own viewing comes first.
- **Cast to Kodi - and to an Apple TV from a Mac.** Kodi shows up beside the Chromecasts and TVs; on macOS, AirPlay sends to an Apple TV with Apple's own button. A channel you pick while casting goes to the same TV, the cast strip goes back in the rewind buffer or the archive, and a device on another part of your network can be added by its address.
- **Teletext and DVB subtitles.** A channel's own subtitles in the subtitle menu, by language and page, and a teletext viewer with the colour keys - press `T`. It reads dopeIPTV's own rewind buffer, so it never takes a second connection.
- **Find anything with `Ctrl`+`K`** (`⌘`+`K` on a Mac): an action, a setting, a channel or a programme, from one field.
- **Watch together.** Start a room, share its code, and friends watch the same film, episode or channel in step - each from their own source. Encrypted with a key made from the code; dopeiptv.org only passes the messages on.
- **Radio, YouTube and Twitch.** Internet radio from radio-browser.info by country, language and genre, under Local files; and YouTube Live, Twitch and similar sites through yt-dlp - **Open address…** or `Ctrl`+`L`.
- **Sync between computers, for everyone** - no supporter key needed. Encrypted on your computer with a code only your computers know; dopeiptv.org can't read what's synced. **Settings → Sync.**

### Also new

- **Night mode** for the sound: loud adverts down, quiet dialogue up - press `N`.
- **Recordings saved as MP4**, if you like: nothing re-encoded, and the original kept if anything goes wrong.
- **Series rules that remind you** of every airing instead of recording it.
- **Your statistics**: what you watch by week, month and year, your top channels and titles - counted on this computer only.
- **Export to calendar**: your reminders and planned recordings as a calendar file, or a calendar to subscribe to.
- **Simkl** beside Trakt, and **Discord status** if you want it.
- **The system's media controls on macOS and Windows**: Control Center, the lock screen and AirPods on a Mac; the media box by the volume and the lock screen on Windows.
- **Stream information**: resolution, frame rate and codec when you change channels and in the channel list - off by default, under Settings → Playback.
- **The connections in use on your account**, when the provider says, and a plain warning before the app would go over the limit - a cast counts too.
- **Official builds say so**: every download carries a signed list of its files, and a changed copy leaves dopeiptv.org's services alone.
- **More Nerd:** Test connection, backup addresses for a source, own DNS over HTTPS, a trusted certificate for a server of your own, low latency for live, stream analysis (a simple TR 101 290), Home Assistant's commands over MQTT, and searching the other parts of a home network split in VLANs.

### Better

- **Settings in groups** - Watch, Content, Accounts and integrations, System and Nerd - each setting in one place, and **a page of its own for each source**. Keyboard shortcuts have a page of their own too. Settings also opens in about half the time, and the video beside it no longer stutters.
- **The source you are in, in plain sight**: its name over the categories and in the status line.
- **Asking for a donation, more calmly**: the heart shows one week a month once you use the app for real, and goes by itself. "Not right now" puts it away for three months, "I've donated" for a year.
- Casting, multiview and the phone remote follow the same rules as the player: the VPN guard, the account's connection limit, and private viewing to the end.
- On macOS and Windows, opening a video, a folder or a backup uses the system's own file dialog.

### Fixed

- "Cast to TV" was listed twice in the video's right-click menu.
- A channel previewed after music showed the visualiser instead of its picture.
- A reminder set from the guide forgot when its programme ends.
- With mpv 0.42, the highest quality for adaptive streams stayed on the lowest.
- macOS: a rare crash when an audio device came or went as the player closed.
- Windows: a cast no longer waits seconds to start, and the app never shares a port another program is using.
- The guide's compact bar with a large font, and a light strip at the end of table headers in dark themes.
- An address that gave a web page instead of a stream was asked for again and again by the rewind buffer.
- Failover could pick a channel the other source hides or locks.

On the AppImage from 1.2.15 or later, or the Windows or Mac app from 1.4.5? Click **Update now** in the banner - or download 1.6.0 from [dopeiptv.org](https://dopeiptv.org).
