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

Live streams now use mpv's temporary on-disk packet cache. A separate DVR row
under the video shows the contiguous cached interval around the playhead,
supports clicking/dragging within it and returns to the newest cached data
with LIVE. The cache starts when the channel is opened and is deleted when
the stream closes; it is not a permanent recording. The provider archive
timeline remains independent. The amount available depends on what mpv has
actually cached, not the provider's EPG or an assumed programme length.

Flags are displayed at 16 x 12 logical pixels to match the smaller category
icons.

Validation on Windows: Ruff, mypy and 46 playback/display tests pass. A real
libmpv smoke check with a synthetic HTTP stream verifies disk caching,
rewinding, LIVE and stop cleanup using a single connection. An offscreen
MainWindow check with libmpv loaded covers resizing, a smaller main window,
pop-out and docking back. The broader suite has existing Windows failures;
the failing tests also fail against unmodified upstream testing.
