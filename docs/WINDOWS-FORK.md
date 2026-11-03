# Windows display fixes

This fork adds offline SVG flags to category rows, including flag emoji and
country prefixes such as IT, UK, USA, DE and FR. The provider's category names
and identifiers are unchanged. Flags are from flag-icons 7.5.0 (MIT); the
license and source are included in `dopeiptv/assets/flags/`.

Drag the small horizontal handle immediately below the docked video to resize
it vertically. The existing column divider changes its width. The video keeps
its original aspect ratio with the default Auto aspect setting; unused space
is filled by the player. Height is remembered and limited to the available
space, leaving the programme details accessible. Fullscreen and pop-out keep
their existing sizing behavior.

These changes do not add a local DVR or change catch-up/buffering behavior.

Validation on Windows: Ruff, mypy and 15 focused tests pass. An offscreen
MainWindow check with libmpv loaded covers resizing, a smaller main window,
pop-out and docking back. The broader suite has existing Windows failures;
the failing tests also fail against unmodified upstream testing.
