## dopeIPTV 1.2.7

Casting that actually works on a television.

- **The overlay on the TV is gone.** A title bar, a scrub bar and a LIVE badge
  were drawn over the picture and never went away — on channels, films and
  timeshift alike. The root cause was the receiver redrawing its chrome on
  every BUFFERING → PLAYING transition: a stream that rebuffered kept it
  visible permanently. Converted streams are now announced as LIVE with no
  metadata, so the receiver draws nothing at all. Native films keep their own
  chrome (BUFFERED with a duration), where the bar is real and can be dragged
  with the television's own remote.

- **Subtitles travel beside the picture, not burned in.** A text subtitle used
  to be drawn into the video with ffmpeg's subtitles filter, which read the
  entire file before the first frame — measured: 100 % of a film downloaded
  before a single line was shown. It now rides as a WebVTT rendition in an HLS
  playlist, which costs one provider connection, starts in under a second, and
  needs no libass. macOS no longer requires the homebrew-ffmpeg tap.

- **Subtitle timing is correct.** ffmpeg writes no X-TIMESTAMP-MAP in its
  WebVTT segments; without it the receiver cannot place the cues against the
  picture and shows nothing. The measured transport-stream preload is injected
  on the way out.

- **Subtitle activation no longer deadlocks.** enable_subtitle was called from
  pychromecast's receive thread, which then waited for the answer on the same
  thread. Dispatched to its own thread now.

- **Switching subtitle mid-film works.** The receiver reports the old stream's
  track list for a moment after a new one is loaded. Track IDs are identical
  (ffmpeg builds them the same way), so the old report was mistaken for the
  new one and the subtitle was never switched on. Gated on media_session_id
  now.

- **The cast strip wraps instead of overlapping.** The controls in the
  right-hand column sat on top of one another when the column was pulled
  narrow. A new FlowRow layout wraps items onto the next line and grows the
  strip to hold them.

- **The seek bar click lands where it was aimed.** Qt insets the groove by half
  a handle at each end; the old x/width calculation put a click up to half a
  handle from the target. Uses QStyle.sliderValueFromPosition now.

- **Three device tiers instead of a checkbox.** "Which Chromecast is this?"
  offers Original (no limit), HD (1080p, any frame rate) and Oldest (1080p30),
  named after what the device is rather than its spec. Old settings are mapped
  automatically.

- **Timeshift rewind no longer crashes.** An archive stream joined mid-GOP
  could have its SPS past 1 MB of data; the 1 MB probe limit meant ffmpeg
  never learned the picture size. Timeshift gets 8 MB now.

- **4K is not passed through to a first-generation dongle.** The frame-rate
  exemption had no size cap, so a 4K/24 film went over untouched to a device
  that cannot decode 4K at any frame rate. Handled by the three-tier quality
  system.

- **The television gets a head start.** The playlist is not handed over until
  three segments have been written — about twelve seconds of lead. Without it
  the receiver raced the converter on expensive sources (4K → 1080p) and won,
  showing a spinner.

- **The player pane hides when casting.** Local playback stops, so the pane
  was just a black rectangle. It now makes room for the cast strip.

- **The cast clock counts between reports.** The receiver sends its position
  when something happens to it, not once a second. The clock now interpolates
  with monotonic time while the picture is playing.

Full details in the [changelog](https://github.com/slimture/dopeIPTV/blob/main/CHANGELOG.md).

> Linux is and remains the primary target — Windows and macOS are a bonus.
