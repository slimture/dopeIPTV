## dopeIPTV 1.2.10

A macOS release: fullscreen stopped freezing, and a few things around
watching a series in a row.

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
