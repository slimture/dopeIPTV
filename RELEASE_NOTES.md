## dopeIPTV 1.6.11

**Pick the app's icon and a calmer TV list - and a large round of security work: no stream address or login on another program's command line, every internet stream's certificate checked, and a backup without a password holds no login.**

### Added

- **App icon:** pick it under Settings → Appearance → App icon - the blue one, a heart, one in your accent colour (it follows the accent), midnight, paper, ocean, forest, sunset, grape, a round one or a retro TV.
- **Show channel numbers** under Settings → Appearance. The TV list shows no numbers by default now, with more room for the names; typing a number still zaps to its channel.
- A notice's words show in full when you click them.

### Security and privacy

- **Nothing on a command line:** a recording, a cast's converter, AirPlay and server mode never hand another program a provider's address, login or token. They read the stream through the app's own bridge, or from a private file only your user can read - another account on the computer sees nothing of it.
- **The bridge guards every connection** a stream makes - its segments, keys and redirects - so a playlist from the internet cannot reach this computer, the router or a device at home. The in-app player reads every internet stream through it, Nerd on or off; your own proxy goes behind it.
- **Certificates:** every https stream from the internet has its certificate checked, in the player and in recordings. One that does not check out plays nothing and says why, and the app asks once whether that source may play without the check - an answer you can take back on the source's page.
- **Backups:** a backup without a password holds no login, and putting it back keeps the logins already on the computer. With a password the whole file is encrypted, as before. A restore brings nothing that runs something, lets others in or sends connections elsewhere unless you tick it.
- **Kodi:** its login is never sent over plain http without asking - Send anyway, Use HTTPS (tried first) or Cancel.
- **Kids profiles** can be kept to the categories a grown-up picks, and PINs can be 4 to 12 digits.
- **Watch together:** every request to the relay carries the room's credential, and nothing said before you joined is played back after a restart.
- **Sync:** a computer joining is asked before it takes a copy saved more than 30 days ago.
- The remote API and the phone remote hold back a device that asks too often or guesses tokens; the app's own yt-dlp is checked before every run; a login is masked in the log in its encoded forms too; the subtitle search waits for Search while you watch privately; and adding an Xtream server or portal on plain http says the login travels unencrypted.

### Removed

- **The supporter key**, everywhere. The heart icon is one of the icons to pick.

### Fixed

- A channel in HEVC cast to an Apple TV played sound over a black picture; it now plays, nothing converted - the computer repackages it as the Apple TV wants it.
- The app closed at start when it had been left on Radio.
- Posters went blank after a while in long lists, and stayed so until the app started again.
- The app could crash as Settings opened on a system that writes numbers with a decimal comma (Swedish, German...).
- On Windows, an update installer that Windows would not start - Smart App Control, an antivirus or a policy - was reported as a crash; the update window now says what stopped it and how to install by hand.
- Updating to 1.6.10 could switch the app to the heart icon by itself.
