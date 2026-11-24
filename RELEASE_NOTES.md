## dopeIPTV 1.7.0

**Download films and episodes to watch without the network, offline mode, every source's channels in one list, a new first-run guide with a demo - and every message in the app's own look.**

### Added

- **Downloads:** *Download* on a film or an episode - or *Download this season* / *Download all episodes* on a series - keeps it on your computer, one at a time over one connection. A download goes at a calm pace, a few times as fast as the title plays, and the next waits a few minutes, so to the provider it looks like someone watching (*Download at full speed* under Settings → Recording → Downloads takes that away). A cut download carries on where it stopped. Downloaded titles play their file as the same title, with your resume point and watched mark. **Downloaded** in the sidebar shows them all.
- **Offline mode:** comes on by itself when the network is gone, or from File → Work offline. Movies and Series then show what is downloaded, and nothing goes to the internet - no update check, artwork, sync or provider. It follows the system's own word on the network and never stops what is playing.
- **Every source:** with two sources or more, TV has *Every source* under All - the channels of all your sources in one list. A channel of another source plays from it without switching, with what is on from the guides already on your computer.
- **A new first-run guide:** pick what you want to watch - an IPTV provider, a media server, a box at home or just your own files and radio - and Connect says what it is doing, then how many channels, movies and series came, or in plain words what went wrong and what to do. Nothing is saved until it worked.
- **A new demo** with free, legal test streams and open films, no account needed. Nothing of it is kept.
- **Start on the last channel** (Settings → General, off by default, per profile): the app opens on the channel you watched last, like a TV switched on.
- **Mine's favourite folders** hold the favourites of every source - "Sport" of two sources is one folder - and a title dragged in one stays where you drop it.
- **Notifications fold away** to a small bell when nothing happens; its panel has a **History** of what the app said and did since it started (kept in memory only, nothing while viewing privately).
- **Recordings and the window:** while `dopeiptv --server` runs, closing the window as it records hands the rest of the recording to the server. With no server, closing the window with a recording running or due within 12 hours asks first.
- Playlists sent gzipped, and lists of bare stream addresses, are read; a playlist file's path pasted where the server goes switches to M3U.

### Changed

- **Every message and question** - Reset all, a delete, a wrong PIN, a name asked for - is in the app's own look on every theme instead of the desktop's grey boxes.
- **Enter never stops a recording or deletes a download:** in those questions it answers the safe choice; Stop and Delete take a click.
- Every source's programme descriptions come at once on a very large guide.
- An AppImage updates itself in a folder its group may change only when no other account is in that group.
- The app's new texts read better in all 26 other languages.

### Fixed

- A series' title said the series' name over and over in the window's title and History; an episode is now "Show · S2 E3 · Title", everywhere.
- A provider's error message could show the whole address it failed on, with the login in it; every notice now shows an address as its server alone.
- With server mode on in the window, a scheduled recording could take the connection of a channel a TV was watching through it; it now waits for room, or records from that channel's buffer.
- A source added without a name was named after its address with the login in it; it is named by its server now, and older ones are renamed at the first start.
- Muting a video no longer makes the picture stall for a moment.
- "Settings › Picture & sound…" in the player's menu showed as "Picture_sound".
- After an AppImage update the window could come up beside its pinned dock icon.
- The cast window no longer makes GNOME's pointer spin as it opens.
- The notifications panel takes at most 40 % of the window; the rest scrolls.
- A series marked watched as a whole now shows every episode as watched.
- The first-run guide no longer comes up over TV mode, loses the keyboard to the list behind it, or plays a file opened at the first start under itself.
- One damaged download file can no longer hide every download.
