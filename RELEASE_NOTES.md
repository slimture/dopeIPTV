## dopeIPTV 1.2.13

A Linux release: the AppImage and the .deb start on systems where they
could not, and say why when something does go wrong.

### Linux

- **Starts on Debian 13, Ubuntu 24.04 and newer with Mesa graphics.** The
  download carried its own copies of the C++ runtime and the Wayland
  libraries, older than the ones a newer system's graphics driver needs.
  With ours loaded first the driver could not load, and the app either
  quit at start or never showed its window. Both now come from your
  system. Thanks to @lmerega for tracking down the Wayland half (#20).
- **Starts on a lean X11 system.** Four small X11 libraries Qt needs were
  missing from the download, so a system without them could not open a
  window at all. They are included now.
- **Starts without 3D graphics.** In a virtual machine without 3D
  acceleration, or over VNC or remote X, the app quit on launch. It now
  opens and plays through an external player instead.
- **A crash says what happened.** Qt's own error messages never reached
  the log, so a failed start said only "Aborted". They are logged now.

### Under the hood

- Every release is now started for real before it ships: on Ubuntu
  22.04 and 24.04 and Debian 12 and 13, under X11, and on Debian 13 also
  under Wayland, where the main window has to draw - on both x86_64 and
  ARM.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target - Windows and macOS are a bonus.
