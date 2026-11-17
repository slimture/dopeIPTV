## dopeIPTV 1.6.1

**Smooth scrolling while video plays, a calmer guide, a night mode that tames loud adverts and bangs, the visualizer for music channels, and your settings on a new computer in one step.**

### New and better

- **Smooth while something plays.** The picture is now drawn on its own, apart from the rest of the window: scrolling a list, the guide or a series' page stays smooth while a channel, a film or the visualizer plays, and nothing in the window can make the video stutter. If a computer cannot draw it that way, dopeIPTV uses the old way by itself - **Settings → Nerd → Diagnostics** says which.
- **A calmer, denser guide.** Lower rows, so more channels fit; a narrower channel column that shows only the logos when the guide is narrow (drag its edge to set the width); flat cards with a thin progress bar on what is on now; separators like "-= Sweden =-" as headings; and rows without guide data left empty instead of repeating the same sentence. Want a line at the time now? **Settings → Guide** turns it on.
- **Night mode that works.** Loud adverts, explosions and bangs come down while ordinary speech stays exactly as it is - it never makes anything louder - press `N`. It says so however you switch it, and tells you when it could not be turned on for a stream.
- **The visualizer for music and radio channels.** A channel in your playlist with sound but no picture now shows the visualizer instead of a black screen. Two new styles, Vectorscope and Frequencies, make five - in the player's options menu.
- **New computer? Bring your settings from the welcome.** Type the sync code from your other computer, or restore a backup: your profiles and settings come over, and your sources too when the other computer syncs them. Encrypted on your computer before it is sent - dopeiptv.org keeps only bytes it cannot read.
- **The same menu bar everywhere.** File, View, History and Help on Windows, Linux and macOS - About is under Help (on a Mac in the dopeIPTV menu, as before). **Settings → General** now ends with About and the version too.
- **Server mode tells you who has its port** - dopeIPTV's own server started earlier, or another program - and offers the next free one with one click.
- **Local files never freeze the window.** Searching, switching to folders and opening a big folder on a network drive read the disk in the background - the window stays free and the rows fill in as they come. **Rescan** (the button beside the view switch, or `F5`) reads a folder again.
- **Settings open at once** - about four times faster, and on GNOME without the spinning pointer that hung around for half a minute.
- **Pictures load without slowing anything down.** Posters, logos and episode pictures are decoded once, away from the window, so lists, Home and above all a series' page scroll smoothly while they load.
- **The phone remote plays everything the app plays.** Movies and series by category, Continue watching and History, Local files with their search, radio and recordings - and while an episode plays, its season's episodes with the one playing marked, previous and next episode on the buttons. Seek in a film or an episode, pick subtitles and sound, set the sleep timer, and record what plays or the next programme - it asks first, and any question the app would ask comes to the phone. Searching opens the phone's own keyboard, in TV mode too.
- **The phone remote fits small phones** - down to the smallest iPhone SE, upright or on its side - and **follows what plays**: change channel anywhere and the page marks it and scrolls to it.
- **The middle of the window follows what plays.** Start a channel from the phone or the media keys while a series' page shows, and the channel list comes up with it marked - the same for a film, an episode, a file, a station or a recording. Not while you type, in a dialog or in TV mode.

### Fixed

- Keyboard shortcuts sync between your computers again - between computers of the same system, since `⌘` is not `Ctrl`.
- A series' page played an episode on one click. Now one click picks it and a double click or `Enter` plays it, as in the lists - unless **Auto-play preview on selection** is on.
- Connections to your account are counted right: a cast on the source it comes from, a stream the player gave up on no longer, a rewind buffer kept by a recording or a cast only on its own source, and YouTube, Twitch and radio not at all. A cast or a recording after you switched source no longer picks the new source's channel with the same number.
- Searching in a Local files folder finds files in its subfolders too.
- The search boxes say what they search: the list, the categories, or everything.
- TV mode: Back on its Home, twice, leaves it; leaving full screen any way really leaves TV mode, so the next channel no longer brings it back; it opens complete instead of folding in; View > TV mode is checked while it is on; and the window and the docked player come back exactly as they were.
- Leaving full screen put the window back a little lower and smaller each time on Linux.
- A choice in the player's or the guide's right-click menu no longer opens the menu of the row under it.
- A click on the picture shows the controls instead of pausing; the pause button and `Space` pause.
- On a series' page the "Playing" label had its text touching its edges.
- The round pause button over the picture had a square box around it, and the rewind bar came up squeezed for a moment before its normal size.
- On a series' page, the poster's play and pause button under the video now controls the episode that plays.
- Home's back button stays at the top while the page scrolls.
- TV mode: Stop while a dropped channel waited to reconnect now stops it.
- A scheduled recording that comes due while you watch no longer opens a second connection by itself: it records from what plays when it is the same channel, and otherwise waits for a free connection or your OK.
- The same provider account added twice is counted as one account.
- A cast checks the trusted certificate of the source the stream comes from.
- With the phone remote on, a single request could freeze the window for seconds while the guide loaded; it no longer does.
- Smaller things: night mode's notice fits a narrow player, the command palette names the macOS menus right, `N` and `T` get their keys back, and the loading indicator no longer says "Buffering" too early for a playlist address.
- Trakt and Simkl: switching profile quickly while a sign-in was renewed could sign you out.
- The welcome on small screens: the text was cut off and the card ran past the window; the Xtream form also showed fields that belong to other kinds.

On the AppImage from 1.2.15 or later, or the Windows or Mac app from 1.4.5? Click **Update now** in the banner - or download 1.6.1 from [dopeiptv.org](https://dopeiptv.org).

## dopeIPTV 1.6.0

**A profile for everyone at home, a TV mode for the sofa with your phone as its remote, one search across all your sources, and Jellyfin, Emby and Plex as sources.**

### The big ones

- **Profiles.** One per person in the household, each with their own favorites, history, Continue watching, watched marks, Trakt or Simkl, and theme. "Who's watching?" when the app starts, a PIN if you like, and kids profiles that never see a locked or adult category. Switch from your avatar - no restart. **Settings → Profiles.**
- **A page for every series.** Open a series and it gets a page of its own, as on Plex or Jellyfin: the artwork, one button that plays on from where you stopped, the seasons in a row and the episodes as cards with their picture, plot and progress. Back takes you to the list exactly where you were.
- **Search everything at once.** One search across all your sources - channels, what's on in every guide this week, movies and series - with each hit marked with its source. The results stay while you watch, so you can try the next one without searching again. **Search** in the sidebar, or `Ctrl`+`Shift`+`F`.
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

- **You can see a channel loading**: a spinner with the channel's name while it starts, "Still loading" when the provider is slow, and how far a stalled stream has buffered.
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
- **Server mode keeps its guides fresh** by itself, with or without the window.
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
