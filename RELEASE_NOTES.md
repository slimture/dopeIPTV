## dopeIPTV 1.2.10

Fullscreen on macOS stopped freezing, Windows stopped being deleted by
Defender, and a few things around watching a series in a row.

### Windows

- **Defender no longer removes dopeiptv.exe on sight.** It was a false
  positive with two causes, both fixed here. PyInstaller ships the same
  prebuilt launcher in every app built with it, and several engines carry
  signatures for those bytes - so the app was detected for being a
  PyInstaller build rather than for anything it does; that launcher is now
  compiled from source, giving this binary its own bytes. And the .exe
  carried no version resource at all - no company, product or description
  - which is unusual enough for real software to count against it.

  The remaining "unknown publisher" warning needs an authenticode
  signature, which needs a paid certificate. Until then, the README in the
  zip explains the warning, how to allow the app, and how to report the
  false positive to Microsoft - which is what eventually clears it for
  everyone.

### macOS fullscreen

- **The picture no longer freezes when you go fullscreen.** Audio kept
  playing while the image stood still, sometimes for a second or two,
  sometimes until you tabbed out and back. Going fullscreen moves the
  video into a new window, and on macOS a GL surface can keep showing the
  frame it had in the old one while reporting that it is rendering
  perfectly. The player already carried the remedy for this in the pop-out
  path; the fullscreen path never got it.
- **Space pauses again, and every other shortcut works there too.** They
  were bound to the main window, and in fullscreen the active window is
  the one showing the picture - so none of them fired in the one mode
  where they matter most.
- **The pointer hides properly.** It was being hidden on the window behind
  the picture rather than the one in front of it.

### Watching a series

- **A "Next episode" card** appears over the closing minutes, so the
  credits can be skipped with one click. How early it shows is yours to
  set - Settings > Playback, from one to ten minutes, or off. Where the
  credits start varies from show to show, so the app does not guess, and
  it never jumps by itself.
- **Subtitles carry over to the next episode.** Choosing a language on
  episode 1 was forgotten the moment autoplay reached episode 2 - the one
  place it obviously should hold. The choice is now remembered for the
  series, not just for the episode.

### macOS Dock icon

- **Sized like every other icon.** Apple draws an app icon inside its
  canvas - 824 points on a 1024 canvas - and ours was drawn edge to edge,
  so it stood about a quarter larger than its neighbours. It is also drawn
  at full resolution now: the icon had never been rendered above 256 px, so
  the retina Dock had been scaling one up.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target - Windows and macOS are a bonus.
