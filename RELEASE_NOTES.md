## dopeIPTV 1.2.2

The pop-out player works on Linux again, plus programme-guide, recording,
playlist-refresh, buffer and Continue-watching fixes.

- **Pop-out on Linux is fixed — and rewritten.** The pop-out window was black,
  on Wayland *and* X11: on modern Qt/Mesa nothing OpenGL ever reaches the
  screen in a second top-level window. It no longer tries — each frame is now
  rendered offscreen in the docked player's own working context and drawn into
  the pop-out as an image. The docked player's mpv instance is never moved or
  freed, so popping out can't disturb playback and docking back is instant.
- **No more stuttering in the pop-out.** mpv was blocking the interface until
  each frame's display time (worst on 23.976 fps film). It no longer paces us,
  frames are read back without waiting on the GPU, and only when there is
  actually a new one.
- **The docked mini player behaves while popped out**: no leftover strip where
  the control bar used to be, no frozen frame showing through, and the bar's
  menus (subtitles, record, timeshift) open properly over the pop-out. Docking
  back no longer leaves the video paused.
- **"Refresh playlist" really refreshes.** Within five minutes of the last
  fetch it silently served the cached lists back, so new channels or films at
  your provider never showed up.
- **The network-buffer setting now bites.** It applies to the stream you're
  watching (not just the next one), and a larger value also raises the memory
  budget — so it genuinely helps against stuttery live channels.
- **Continue watching / History**: a film first played from History could not
  be resumed (stream error) and showed up twice in History with one copy
  unplayable; the same titles did nothing when clicked on Home. Fixed, along
  with a crash when clicking a Home card.
- **The programme guide is tidier**: channel logos now show the first time you
  open it (not only the second), a programme is selected by clicking its box
  rather than its title, and the buttons along the bottom are finally all the
  same size.
- **Recordings start where you are.** With a large buffer the file used to
  begin well before the frame on screen, because the already-played part of the
  buffer was written out too.
- **Smaller things**: the pop-out and volume buttons are drawn to match the
  other controls on Linux, the collapsed sidebar is remembered between
  sessions, and sending a film to multiview continues from where you were.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target — Windows and macOS are a bonus.
