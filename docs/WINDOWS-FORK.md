# Windows display fixes

This fork adds offline SVG flags to category rows, including flag emoji and
country prefixes such as IT, UK, USA, DE and FR. The provider's category names
and identifiers are unchanged. Flags are from flag-icons 7.5.0 (MIT); the
license and source are included in `dopeiptv/assets/flags/`.

Drag the small horizontal handle immediately below the docked video to resize
it vertically. The existing column divider changes its width. The video keeps
its original aspect ratio with the default Auto aspect setting; unused space
is filled by the player. Height is remembered and limited to the available
space, leaving the programme details accessible. A detached Windows window
can be resized from its edges and corners even with its title bar hidden.
Docking restores the original layout slot and reapplies the height handle's
constraints, so repeated detach/dock cycles do not pin the docked size.

Live streams now use mpv's temporary on-disk packet cache. A separate DVR row
under the video shows the contiguous cached interval around the playhead,
supports clicking/dragging within it and returns to the newest cached data
with LIVE. The cache starts when the channel is opened and is deleted when
the stream closes; it is not a permanent recording. The provider archive
timeline remains independent. The amount available depends on what mpv has
actually cached, not the provider's EPG or an assumed programme length.
The DVR row hides after 2.5 seconds of inactivity. Moving over the video or
the DVR controls reveals it; it stays visible during scrubbing or an open
menu. Position polling does not reveal an idle-hidden row.

Flags are displayed at 16 x 12 logical pixels to match the smaller category
icons.

All 27 original locales are retained. The new DVR controls reuse translated
strings and update when the language is switched at runtime.

Windows source builds use the committed original `windows-256.png` when the
CI-generated `.ico` is absent. Install Pillow alongside PyInstaller for that
conversion; this keeps local builds from silently using PyInstaller's icon.
The original logo assets and runtime icon drawing are unchanged.

Graphite and OLED screenshots under `docs/screenshots/windows-player-*.png`
use an isolated demo UI fixture and a synthetic cache interval, with no
provider account or user data.

Validation on Windows: Ruff, mypy and 56 playback/display/i18n tests pass. A real
libmpv smoke check with a synthetic HTTP stream verifies disk caching,
rewinding, LIVE and stop cleanup using a single connection. An offscreen
MainWindow check with libmpv loaded covers resizing, a smaller main window,
pop-out and docking back. Pointer-event regression tests cover corner drags,
three detach/dock cycles followed by increasing the height, idle hide/reveal,
and the new controls in all 27 locales. The broader suite has existing Windows failures;
the failing tests also fail against unmodified upstream testing.
