## dopeIPTV 1.2.3

Resizable video windows, sections that remember where you were, and a cast
panel that finally lists the right films.

- **Resize the pop-out and the multiview grid without the title bar.** Both are
  title-bar-less by default, which left them with no resize grips at all — the
  only way to change their size was to turn the title bar back on. The video's
  own edges are the handles now, with the matching cursor when you hover one.
- **The pointer no longer drags across a maximized video.** Every mouse report
  was redrawing the centre play/pause glyph and re-placing the seek bar,
  timeshift timeline and sleep pill — and each of those repaints the video
  underneath. Measured on 300 pointer events: 299 repaints before, 1 after.
- **Every section remembers the sub-category you were in.** Jumping between TV,
  Movies, Series, Favorites and back dropped you at the top of the list every
  time. Also for Watch Later, Watched, Recordings and History.
- **Cast members are no longer credited with films they are not in.** Titles
  were matched against your playlist with spaces stripped, so a credit like
  "America" claimed every "American ..." title on it. "The Wall" is not "The
  Wall Street Documentary" — while genuine release junk ("Inception REPACK")
  still matches.
- **The cast panel behaves like the rest of the app**: picking a title starts it
  and lands the middle column on it in its own category with the detail panel
  filled in (it used to just play, with nothing selected), shows drill into
  their episode list, and missing posters are resolved the same way the middle
  list resolves them.
- **Unmuting a film that started muted gives sound back** without replaying it.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target — Windows and macOS are a bonus.
