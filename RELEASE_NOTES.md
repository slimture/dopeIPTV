## dopeIPTV 1.2.9

Your own files, played like everything else - and autoplay works again.

### Local files

A new section that plays what is on your disk, and on any share the system
has already mounted. A gvfs SMB mount on Linux and a `/Volumes` share on
macOS are ordinary paths, so network files come along for free with no
in-app SMB client to go wrong.

- **Two ways to look at a library.** Plain folder browsing, or an
  Infuse-style view that reads the file names and groups episodes into
  series and seasons, films under Movies, and everything else under its own
  heading. The toggle sits in the middle column.
- **Artwork.** Posters from TMDB for films and series that can be matched,
  a frame grabbed with ffmpeg for the home videos that cannot, and embedded
  or folder cover art for music. Nothing is guessed at: a file with no year
  and no release tag in its name gets an honest thumbnail rather than the
  poster of whichever film happened to share a word with it.
- **Music, as music.** Albums shelved together instead of a heap of files,
  artist/album/title read straight from the tags (FLAC, MP3, MP4 - no extra
  dependency), a play queue you can add to by right-clicking, a visualiser
  in place of the black rectangle, and a ten-band equaliser.
- **Everything the rest of the app does.** Resume where you left off, an
  entry in History under its own category, casting, multiview, and Trakt
  scrobbling for the files that TMDB can name. A file on a share that is no
  longer mounted says so and offers to drop the row, rather than failing
  silently.
- **A settings section of its own**, for the folders to watch and for
  clearing the caches.
- **Scanning says what it is doing** - files and folders counted so far, and
  where it currently is - and results appear as they are found instead of
  after the whole tree has been walked.

### Autoplay

- **Nothing autoplayed: not the music queue, not local series, not streamed
  episodes.** All three hang off one signal, which was raised only from a
  poll of mpv's `eof-reached` property - true only while `keep-open` is in
  force, and only if a poll happens to catch it. End of playback is now
  taken from mpv's `end-file` event, which is delivered exactly once
  whatever the options say. The poll stays as a backup.
- **The next episode no longer comes up paused.** `keep-open` pauses mpv
  when a file ends, and that pause is player state, not file state - it
  survived loading the next file.
- **Local series play through.** A local episode is not a provider episode
  and never reached the autoplay branch, so every series stopped dead at
  each episode's end. Music always plays on; video only when it is actually
  an episode, so one film ending never starts an unrelated one.

### Fixes

- **An already-watched episode can be replayed from History.** History rows
  carry the series context but no provider episode id, and the URL was built
  from that missing id - producing a path that never existed. The stored URL
  is used when there is no id.
- **Artwork is fetched for a whole shelf, not just the top of it.** Every
  lookup stopped after its first batch, so in a large folder only the first
  rows ever got a poster.
- **The file work moved off the UI thread.** Listing a folder, reading tags
  and walking a library are all round trips over SMB, and on the UI thread
  they were the macOS spinning beachball.
- **A frozen picture in macOS fullscreen heals itself** instead of waiting
  for the user to tab out and back.
- **A plot summary is never rendered as markup.** The music panel switches
  that label to rich text, and it was not switched back.

### Builds

- **Windows on ARM64** gets a native build.
- Both macOS builds now run on macOS 15. The Intel one already did - it is
  the only x86_64 image left - and the Apple Silicon one moved off an image
  that is being retired in November. Nothing pins a deployment target, so a
  .dmg supports macOS 15 and up.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target - Windows and macOS are a bonus.
