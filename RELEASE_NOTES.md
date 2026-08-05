## dopeIPTV 1.2.8

Two first-run bugs reported by users, fixed - and a build for Intel Macs.

- **The app no longer dies on systems that write 1,5 instead of 1.5.** On a
  German, French or most other non-English Linux systems the app closed
  without a word the moment the video player initialised - which on a fresh
  install was the wizard's "Try demo" or "Continue without account" button.
  libmpv hard-requires the C numeric locale and crashes on anything else;
  Qt sets the locale from the system at startup. The numeric locale is now
  reset right before the player is created, the way libmpv prescribes.
  Same crash in the .deb, AppImage and Flatpak alike; fixed in all of them.
  (#18)

- **Pasting an M3U link no longer flips the type to Xtream.** A playlist URL
  with a deep path (github.com/user/repo/main/list.m3u) was mistaken for an
  Xtream stream URL, with path segments read as username and password. A
  link whose path ends in .m3u/.m3u8 is now always treated as the playlist
  it is.

- **Intel Macs get a build again.** The .dmg was Apple Silicon only; the
  release now ships both `macOS-arm64` and `macOS-x86_64`.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target - Windows and macOS are a bonus.
