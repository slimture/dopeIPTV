## dopeIPTV 1.6.12

**A security round from two independent reviews of 1.6.11: nothing the app sends to dopeiptv.org can carry your logins, updates are checked once more before they run, and guide descriptions no longer show HTML code.**

### Security and privacy

- **Your logins never reach us:** every request the app makes passes one check before it is sent, and a request to dopeiptv.org that holds any of your logins - a provider's user name, password or token, a box's MAC address, your Trakt, Simkl, OpenSubtitles or Kodi login, or the sync code - is not sent, in whatever form it would travel.
- **Updates** are checked once more right before they run or replace the app, and an AppImage in a folder other accounts may change is not updated there - move it to a folder of your own, or update by hand.
- **Private viewing:** turning it on while a recording or a cast still writes the channel to the disk shows a card - stop it, or let it finish.
- **Watch together** works with members on 1.6.11 and later; someone on an older version is told to update.
- **Settings files:** the app asks about every settings file other accounts on the computer could read - also one that belongs to another account, or points to a file that is not there.
- The log never writes a line it could not clean.

### Fixed

- A programme's description that came with HTML from the provider showed the code under the video; now only the words show. The same for a film's plot.
